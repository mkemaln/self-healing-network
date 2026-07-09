import random
import time
import os
import logging
import datetime
import numpy as np
import paramiko

from sb3_contrib import MaskablePPO
from sb3_contrib.common.maskable.policies import MaskableActorCriticPolicy
from sb3_contrib.common.wrappers import ActionMasker
from self_healing_env import SelfHealingEnv


GNS3_SSH = {
    "host": "192.168.158.128",
    "user": "gns3",
    "password": "gns3",
    "bridge": "br0",
    # "ovs_container": "OpenvSwitch-1",
    "ovs_container": "GNS3.OpenvSwitch-1.03cc82b5-8a13-43d7-81ed-8e7439855cf8",
    "shell_menu_key": "4",
}


LOG_DIR = "logs"


def setup_logging(name="train"):
    os.makedirs(LOG_DIR, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(LOG_DIR, f"{name}_{ts}.log")

    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)

    if not logger.handlers:
        fh = logging.FileHandler(path)
        fh.setLevel(logging.DEBUG)
        ch = logging.StreamHandler()
        ch.setLevel(logging.INFO)

        fmt = logging.Formatter(
            "[%(asctime)s] %(levelname)-5s %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        fh.setFormatter(fmt)
        ch.setFormatter(fmt)
        logger.addHandler(fh)
        logger.addHandler(ch)

    return logger


def gns3_port_for_link(link_idx):   
    return str(link_idx + 1)


def _gns3_shell(docker_cmd, timeout=5):
    """SSH -> menu(sel key) -> shell -> docker exec <container> <cmd>."""
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(GNS3_SSH["host"], username=GNS3_SSH["user"],
                   password=GNS3_SSH["password"])

    chan = client.invoke_shell()
    time.sleep(1)

    out = b""
    while chan.recv_ready():
        out += chan.recv(4096)
        time.sleep(0.2)

    # Forward menu — press key for "shell" option
    if b"information" in out.lower() and b"shell" in out.lower():
        chan.send(GNS3_SSH["shell_menu_key"] + "\n")
        time.sleep(1)
        while chan.recv_ready():
            chan.recv(4096)
            time.sleep(0.1)

    full_cmd = f"docker exec {GNS3_SSH['ovs_container']} {docker_cmd}\n"
    chan.send(full_cmd)
    time.sleep(1)

    out = b""
    while timeout > 0:
        if chan.recv_ready():
            out += chan.recv(4096)
        time.sleep(0.2)
        timeout -= 0.2

    client.close()
    return out.decode(errors="replace")


def set_link_state(port, down=True):
    state = "down" if down else "up"
    cmd = f"ovs-ofctl mod-port {GNS3_SSH['bridge']} {port} {state}"
    try:
        result = _gns3_shell(cmd)
        if "error" in result.lower():
            print(f"[FAILURE] ovs-ofctl error: {result.strip()}")
        else:
            print(f"[OK] Link {port} {state}")
    except Exception as e:
        print(f"[FAILURE] SSH/docker failed: {e}")


def make_env(simulation=False, num_links=4):
    env = SelfHealingEnv(
        odl_url="http://192.168.158.130:8181",
        num_links=num_links,
        simulation_mode=simulation,
        convergence_delay=1.0 if simulation else 3.0,
        max_episode_steps=50,
        flapping_window=5,
        throughput_weight=2.0,
        loss_weight=5.0,
        connectivity_weight=3.0,
        action_penalty=0.1,
        healthy_threshold=0.05,
    )
    env = ActionMasker(env, lambda e: e.get_action_mask())
    return env


def train_simulation(total_timesteps=50000):
    log = setup_logging("train_sim")
    episodes = total_timesteps // 256

    log.info("=" * 60)
    log.info("TRAINING MODE: Simulation  |  timesteps=%d  episodes=%d", total_timesteps, episodes)
    log.info("hyperparams: lr=0.001  gamma=0.95  n_steps=128  max_ep_len=50")
    log.info("=" * 60)

    env = make_env(simulation=True, num_links=4)
    model = MaskablePPO(
        MaskableActorCriticPolicy,
        env,
        verbose=1,
        learning_rate=0.001,
        gamma=0.95,
        n_steps=128,
    )

    inner = env.env

    for episode in range(episodes):
        link_idx = random.randint(0, inner.num_links - 1)
        severity = random.uniform(0.6, 0.95)
        obs, _ = env.reset(options={
            "failure_link": link_idx,
            "failure_severity": severity,
        })

        log.info("Episode %3d/%d | fail link=%d severity=%.2f",
                 episode + 1, episodes, link_idx, severity)

        episode_reward = 0
        done = truncated = False
        for step_idx in range(50):
            action_masks = env.action_masks()
            if not np.any(action_masks[1:]):
                log.debug("  no valid reroute targets — ending early")
                break
            action, _ = model.predict(obs, action_masks=action_masks, deterministic=False)
            obs, reward, done, truncated, info = env.step(action)
            episode_reward += reward
            log.debug("  step %2d | action=%d reward=%+.3f done=%s trnc=%s",
                      step_idx + 1, action, reward, done, truncated)
            if done or truncated:
                break

        avg_r = episode_reward / max(step_idx + 1, 1)
        log.info("  -> reward=%.2f  avg=%.3f  steps=%d  done=%s",
                 episode_reward, avg_r, step_idx + 1, done or truncated)

    model.save("self_healing_sim")
    log.info("Training complete. Model saved to self_healing_sim.zip")
    env.close()


def train_real(total_episodes=100, timesteps_per_episode=50):
    log = setup_logging("train_real")

    log.info("=" * 60)
    log.info("TRAINING MODE: Real network (GNS3 + ODL)")
    log.info("episodes=%d  steps_per_ep=%d  num_links=4", total_episodes, timesteps_per_episode)
    log.info("hyperparams: lr=0.0003  gamma=0.95  n_steps=128")
    log.info("=" * 60)

    env = make_env(simulation=False, num_links=4)
    model = MaskablePPO(
        MaskableActorCriticPolicy,
        env,
        verbose=1,
        learning_rate=0.0003,
        gamma=0.95,
        n_steps=128,
    )

    inner = env.env
    all_rewards = []

    for episode in range(total_episodes):
        link_idx = random.randint(0, inner.num_links - 1)
        port = gns3_port_for_link(link_idx)

        log.info("Episode %3d/%d | failing link=%d port=%s",
                 episode + 1, total_episodes, link_idx, port)
        set_link_state(port, down=True)
        time.sleep(2)
        obs, _ = env.reset()

        episode_reward = 0.0
        done = truncated = False
        for step_idx in range(timesteps_per_episode):
            action_masks = env.action_masks()
            if not np.any(action_masks[1:]):
                log.debug("  no valid reroute targets — ending early")
                break
            action, _ = model.predict(obs, action_masks=action_masks, deterministic=False)
            obs, reward, done, truncated, info = env.step(action)
            episode_reward += reward
            log.debug("  step %2d | action=%d reward=%+.3f done=%s trnc=%s",
                      step_idx + 1, action, reward, done, truncated)
            if done or truncated:
                break

        all_rewards.append(episode_reward)
        avg = np.mean(all_rewards[-20:]) if len(all_rewards) >= 20 else np.mean(all_rewards)
        log.info("  -> reward=%.2f  avg_last_20=%.3f  steps=%d  done=%s",
                 episode_reward, avg, step_idx + 1, done or truncated)

        set_link_state(port, down=False)
        time.sleep(2)

        if (episode + 1) % 20 == 0:
            log.info("Checkpoint: saving to self_healing_checkpoint_%d.zip", episode + 1)
            model.save(f"self_healing_checkpoint_{episode + 1}")

    model.save("self_healing_final")
    log.info("Training complete. Model saved to self_healing_final.zip")
    env.close()


def eval_agent(
    model_path="self_healing_sim",
    num_episodes=3,
    timesteps_per_episode=30,
    inject_failures=True,
):
    log = setup_logging("eval")

    log.info("=" * 60)
    log.info("EVAL MODE: Testing trained agent")
    log.info("model=%s  episodes=%d  steps_per_ep=%d  inject_failures=%s",
             model_path, num_episodes, timesteps_per_episode, inject_failures)
    log.info("=" * 60)

    env = make_env(simulation=False, num_links=4)
    model = MaskablePPO.load(model_path)
    inner = env.env

    for episode in range(num_episodes):
        if inject_failures:
            link_idx = random.randint(0, inner.num_links - 1)
            port = gns3_port_for_link(link_idx)
            log.info("Episode %d/%d | failing link=%d port=%s",
                     episode + 1, num_episodes, link_idx, port)
            set_link_state(port, down=True)
            time.sleep(2)

        obs, _ = env.reset()
        episode_reward = 0.0
        done = truncated = False

        for step in range(timesteps_per_episode):
            action_masks = env.action_masks()
            action, _ = model.predict(obs, action_masks=action_masks, deterministic=True)
            obs, reward, done, truncated, _ = env.step(action)
            action_label = "no-op" if action == 0 else f"reroute link {action - 1}"
            episode_reward += reward
            log.info("  step %2d | %-20s reward=%+.3f done=%s",
                     step + 1, action_label, reward, done)
            if done or truncated:
                break

        if inject_failures:
            set_link_state(port, down=False)
            time.sleep(2)

        log.info("  -> Episode reward: %.2f  network_healthy=%s",
                 episode_reward, "yes" if done else "no")

    env.close()


if __name__ == "__main__":
    import sys

    mode = sys.argv[1] if len(sys.argv) > 1 else "sim"
    arg2 = sys.argv[2] if len(sys.argv) > 2 else None

    if mode == "sim":
        timesteps = int(arg2) if arg2 else 50000
        train_simulation(timesteps)
    elif mode == "real":
        train_real(total_episodes=100)
    elif mode == "eval":
        model_path = arg2 if arg2 else "self_healing_sim"
        eval_agent(model_path)
    else:
        print(f"Usage: python train.py [sim|real|eval] [timesteps|model_path]")
        sys.exit(1)
