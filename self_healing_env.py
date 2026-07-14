import gymnasium as gym
from gymnasium import spaces
import numpy as np
import time
from sdn_client import SDNClient


class SelfHealingEnv(gym.Env):
    """
    Custom environment for RL-driven self-healing SDN via OpenDaylight.

    The agent observes per-link network metrics and can reroute traffic
    away from degraded links.

    Two modes:
      - simulation_mode=True : internal fake network (no real infra needed)
      - simulation_mode=False: talks to real ODL via SDNClient
    """

    metadata = {"render_modes": ["human"]}

    def __init__(
        self,
        odl_url="http://localhost:8181",
        num_links=4,
        simulation_mode=False,
        convergence_delay=2.0,
        max_episode_steps=50,
        flapping_window=5,
        throughput_weight=2.0,
        loss_weight=5.0,
        connectivity_weight=3.0,
        action_penalty=0.1,
        healthy_threshold=0.05,
        fallback_links=None,
    ):
        super().__init__()

        self.num_links = num_links
        self.simulation_mode = simulation_mode
        self.convergence_delay = convergence_delay
        self._max_steps = max_episode_steps
        self.flapping_window = flapping_window
        self.throughput_weight = throughput_weight
        self.loss_weight = loss_weight
        self.connectivity_weight = connectivity_weight
        self.action_penalty = action_penalty
        self.healthy_threshold = healthy_threshold
        self._fallback_links = fallback_links or []

        if not simulation_mode:
            self.client = SDNClient(odl_url)
            self.reroute_fn = self._reroute_via_odl
        else:
            self.client = None
            self._sim = _SimulatedNetwork(num_links)
            self.reroute_fn = self._reroute_simulated

        per_link_features = 4
        flat_dim = num_links * per_link_features + 2
        self.observation_space = spaces.Box(
            low=0.0, high=1.0, shape=(flat_dim,), dtype=np.float32
        )

        self.action_space = spaces.Discrete(num_links + 1)

        self._step_count = 0
        self._link_reroute_count = {}
        self._prev_obs = None
        self._links = []
        self._reroute_flow_counter = 0

    def _build_real_state(self):
        nodes = self.client.get_nodes()
        self._links = self.client.get_links(fallback=self._fallback_links)
        port_stats = self.client.get_all_port_stats()

        n_active = 0
        total_tx_rate = 0.0
        avg_loss = 0.0
        link_features = []

        for i in range(self.num_links):
            if i < len(self._links):
                link = self._links[i]
                src_dev = link["src"]["device"]
                src_port = link["src"]["port"]
                dst_dev = link["dst"]["device"]
                dst_port = link["dst"]["port"]

                src_stats = self._find_stat(port_stats, src_dev, src_port)
                dst_stats = self._find_stat(port_stats, dst_dev, dst_port)

                is_active = src_dev in {n.get("id") for n in nodes}
                tx = 0.0
                rx = 0.0
                loss = 1.0

                if src_stats and dst_stats:
                    tx = float(src_stats.get("bytesSent", 0))
                    rx = float(dst_stats.get("bytesReceived", 0))
                    src_tx_p = float(src_stats.get("packetsSent", 0))
                    dst_rx_p = float(dst_stats.get("packetsReceived", 0))
                    loss = 0.0
                    if src_tx_p > 0 and dst_rx_p >= 0:
                        loss = max(0.0, min(1.0, (src_tx_p - dst_rx_p) / src_tx_p))

                if is_active:
                    n_active += 1
                total_tx_rate += tx
                avg_loss += loss

                recently = 1.0 if self._was_recently_rerouted(i) else 0.0
                tx_norm = min(tx / 1e9, 1.0)
                rx_norm = min(rx / 1e9, 1.0)
                link_features += [tx_norm, rx_norm, loss, recently]

            else:
                link_features += [0.0, 0.0, 1.0, 0.0]

        avg_loss = avg_loss / max(self.num_links, 1)
        health_ratio = n_active / max(self.num_links, 1)
        return np.array(link_features + [avg_loss, health_ratio], dtype=np.float32)

    def _build_sim_state(self):
        link_features = []
        total_loss = 0.0
        n_healthy = 0

        for i in range(self.num_links):
            tx = self._sim.throughputs[i]
            rx = self._sim.throughputs[i]
            loss = self._sim.loss_rates[i]
            active = 1.0 if self._sim.active[i] else 0.0
            n_healthy += active
            total_loss += loss

            recently = 1.0 if self._was_recently_rerouted(i) else 0.0
            tx_norm = min(tx / 1e9, 1.0)
            rx_norm = min(rx / 1e9, 1.0)
            link_features += [tx_norm, rx_norm, loss, recently]

        avg_loss = total_loss / max(self.num_links, 1)
        health_ratio = n_healthy / max(self.num_links, 1)
        return np.array(link_features + [avg_loss, health_ratio], dtype=np.float32)

    def _get_obs(self):
        if self.simulation_mode:
            return self._build_sim_state()
        return self._build_real_state()

    def get_obs(self):
        return self._get_obs()

    def get_action_mask(self):
        mask = np.ones(self.action_space.n, dtype=np.int8)
        for i in range(1, self.action_space.n):
            link_idx = i - 1
            if self._is_link_healthy(link_idx):
                mask[i] = 0
        return mask

    def _is_link_healthy(self, idx):
        if self.simulation_mode:
            loss = self._sim.loss_rates[idx]
            return loss < 0.01 and self._sim.active[idx]
        if self._prev_obs is None:
            return True
        loss = float(self._prev_obs[idx * 4 + 2])
        return loss < 0.01

    def _was_recently_rerouted(self, link_idx):
        last = self._link_reroute_count.get(link_idx)
        if last is None:
            return False
        return (self._step_count - last) <= self.flapping_window

    def _reroute_via_odl(self, link_idx):
        if link_idx >= len(self._links):
            print(f"[REROUTE] No link data for index {link_idx}")
            return True

        link = self._links[link_idx]
        src_node = link["src"]["device"]
        src_port = link["src"]["port"]

        alt_port = self._find_alternative_port(src_node, src_port)
        if not alt_port:
            print(f"[REROUTE] No alternative port found on {src_node} — installing drop flow")
            self._reroute_flow_counter += 1
            self.client.push_flow(src_node, f"reroute-{self._reroute_flow_counter}",
                                  src_port, src_port, priority=200)
            return True

        self._reroute_flow_counter += 1
        flow_id = f"reroute-{self._reroute_flow_counter}"
        ok = self.client.push_flow(src_node, flow_id, src_port, alt_port, priority=200)
        if ok:
            print(f"[REROUTE] Link {link_idx}: {src_node}:{src_port} -> port {alt_port}")
        else:
            print(f"[REROUTE] Failed to push flow for link {link_idx}")
        return ok

    def _find_alternative_port(self, node_id, failed_port):
        connectors = self.client.get_node_connectors(node_id)
        alt_port = None
        for c in connectors:
            port = c.get("id", "").split(":")[-1]
            if port != str(failed_port):
                alt_port = port
                break
        return alt_port

    def _reroute_simulated(self, link_idx):
        self._sim.reroute_away_from(link_idx)
        return True

    def _compute_reward(self, old_obs, obs, action):
        ol = old_obs.reshape(-1)[:-2]
        nl = obs.reshape(-1)[:-2]

        old_tx = ol[0::4]
        new_tx = nl[0::4]
        old_loss = ol[2::4]
        new_loss = nl[2::4]

        old_total_tx = float(np.sum(old_tx))
        new_total_tx = float(np.sum(new_tx))
        old_avg_loss = float(np.mean(old_loss))
        new_avg_loss = float(np.mean(new_loss))

        tx_delta = 0.0
        if old_total_tx > 1e-8:
            tx_delta = (new_total_tx - old_total_tx) / old_total_tx
        tx_delta = np.clip(tx_delta, -1.0, 1.0)

        loss_reduction = old_avg_loss - new_avg_loss
        loss_reduction = np.clip(loss_reduction, -1.0, 1.0)

        old_tx_vals = ol[0::4]
        new_tx_vals = nl[0::4]
        revived = float(np.sum((old_tx_vals < 0.01) & (new_tx_vals >= 0.01)))
        died = float(np.sum((old_tx_vals >= 0.01) & (new_tx_vals < 0.01)))
        connectivity_score = revived - died
        connectivity_score = np.clip(connectivity_score, -3.0, 3.0)

        penalty = -self.action_penalty if action != 0 else 0.0

        reward = (
            self.throughput_weight * tx_delta
            + self.loss_weight * loss_reduction
            + self.connectivity_weight * connectivity_score
            + penalty
        )
        return float(reward)

    def reset(self, seed=None, options=None):
        super().reset(seed=seed)
        self._step_count = 0
        self._link_reroute_count.clear()

        if self.simulation_mode:
            self._sim.reset()
        else:
            self._links = self.client.get_links(fallback=self._fallback_links)

        if options and "failure_link" in options:
            self.inject_failure(
                options["failure_link"],
                down=options.get("failure_down", False),
                severity=options.get("failure_severity", 0.8),
            )

        time.sleep(self.convergence_delay)
        obs = self._get_obs()
        self._prev_obs = obs.copy()
        return obs, {}

    def step(self, action):
        self._step_count += 1

        if action != 0:
            link_idx = action - 1
            self._link_reroute_count[link_idx] = self._step_count
            self.reroute_fn(link_idx)

        time.sleep(self.convergence_delay)
        new_obs = self._get_obs()

        reward = self._compute_reward(self._prev_obs, new_obs, action)
        self._prev_obs = new_obs

        done = self._check_done(new_obs)
        truncated = self._step_count >= self._max_steps
        return new_obs, reward, done, truncated, {}

    def _check_done(self, obs):
        metrics = obs.reshape(-1)[:-2]
        losses = metrics[2::4]
        avg_loss = float(np.mean(losses))
        return avg_loss < self.healthy_threshold

    def render(self):
        pass

    def close(self):
        pass

    def inject_failure(self, link_idx, down=False, severity=0.8):
        if self.simulation_mode:
            if down:
                self._sim.set_link_down(link_idx)
            else:
                self._sim.set_failure(link_idx, severity)

    @staticmethod
    def _find_stat(port_stats, device_id, port):
        if device_id not in port_stats:
            return None
        for entry in port_stats[device_id]:
            if entry.get("port") == str(port):
                return entry
        return None


class _SimulatedNetwork:
    def __init__(self, num_links):
        self.num_links = num_links
        self.reset()

    def reset(self):
        self.throughputs = [1e9] * self.num_links
        self.loss_rates = [0.001] * self.num_links
        self.active = [True] * self.num_links

    def set_failure(self, link_idx, severity=0.8):
        self.throughputs[link_idx] = 1e9 * (1.0 - severity)
        self.loss_rates[link_idx] = 0.3 + severity * 0.5
        self.active[link_idx] = True

    def set_link_down(self, link_idx):
        self.throughputs[link_idx] = 0.0
        self.loss_rates[link_idx] = 1.0
        self.active[link_idx] = False

    def set_link_up(self, link_idx):
        self.throughputs[link_idx] = 1e9
        self.loss_rates[link_idx] = 0.001
        self.active[link_idx] = True

    def reroute_away_from(self, link_idx):
        self.throughputs[link_idx] *= 1.8
        self.loss_rates[link_idx] = max(0.001, self.loss_rates[link_idx] * 0.3)
        if not self.active[link_idx]:
            self.active[link_idx] = True
        for i in range(self.num_links):
            if i != link_idx:
                self.throughputs[i] *= 0.93
                self.loss_rates[i] = min(1.0, self.loss_rates[i] * 1.08)
