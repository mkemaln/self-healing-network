import subprocess
import random
import time
import numpy as np

from sb3_contrib import MaskablePPO
from sb3_contrib.common.maskable.policies import MaskableActorCriticPolicy
from sb3_contrib.common.wrappers import ActionMasker
from self_healing_env import SelfHealingEnv


GNS3_SSH = {
    "host": "192.168.158.130",
    "user": "admin",
    "bridge": "br0",
}


def gns3_port_for_link(link_idx):
    return str(link_idx + 1)


def set_link_state(port, down=True):
    state = "down" if down else "up"
    cmd = (
        f"ssh {GNS3_SSH['user']}@{GNS3_SSH['host']} "
        f"'ovs-ofctl mod-port {GNS3_SSH['bridge']} {port} {state}'"
    )
    try:
        subprocess.run(cmd, shell=True, check=True, timeout=15)
    except subprocess.CalledProcessError as e:
        print(f"[FAILURE] SSH failed: {e}")


def make_env(simulation=False, num_links=4):
    env = SelfHealingEnv(
        odl_url="http://localhost:8181",
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
    print("=" * 60)
    print("TRAINING MODE: Simulation (no real network required)")
    print(f"Timesteps: {total_timesteps}")
    print("=" * 60)

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

    for episode in range(total_timesteps // 256):
        link_idx = random.randint(0, inner.num_links - 1)
        severity = random.uniform(0.6, 0.95)
        obs, _ = env.reset(options={
            "failure_link": link_idx,
            "failure_severity": severity,
        })

        episode_reward = 0
        for step_idx in range(50):
            action_masks = env.action_masks()
            if not np.any(action_masks[1:]):
                break
            action, _ = model.predict(obs, action_masks=action_masks, deterministic=False)
            obs, reward, done, truncated, info = env.step(action)
            episode_reward += reward
            if done or truncated:
                break

        if (episode + 1) % 50 == 0:
            avg_r = episode_reward / max(step_idx + 1, 1)
            print(f"Episode {episode + 1}: total reward {episode_reward:.2f}, avg {avg_r:.3f}")

    model.save("self_healing_sim")
    print("\nSimulation training complete. Model saved to self_healing_sim.zip")
    env.close()


def train_real(total_episodes=100, timesteps_per_episode=50):
    print("=" * 60)
    print("TRAINING MODE: Real network via GNS3 + ODL")
    print(f"Episodes: {total_episodes}  Steps per episode: {timesteps_per_episode}")
    print("=" * 60)

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

        print(f"\n--- Episode {episode + 1}: failing link {link_idx} (port {port}) ---")
        set_link_state(port, down=True)
        time.sleep(2)
        obs, _ = env.reset()

        episode_reward = 0.0
        for step_idx in range(timesteps_per_episode):
            action_masks = env.action_masks()
            if not np.any(action_masks[1:]):
                break
            action, _ = model.predict(obs, action_masks=action_masks, deterministic=False)
            obs, reward, done, truncated, info = env.step(action)
            episode_reward += reward
            if done or truncated:
                break

        all_rewards.append(episode_reward)
        avg = np.mean(all_rewards[-20:]) if len(all_rewards) >= 20 else np.mean(all_rewards)
        print(f"Episode reward: {episode_reward:.2f}  (avg last 20: {avg:.2f})")

        set_link_state(port, down=False)
        time.sleep(2)

        if (episode + 1) % 20 == 0:
            model.save(f"self_healing_checkpoint_{episode + 1}")

    model.save("self_healing_final")
    print("\nReal training complete. Model saved to self_healing_final.zip")
    env.close()


def eval_agent(
    model_path="self_healing_sim",
    num_episodes=3,
    timesteps_per_episode=30,
    inject_failures=True,
):
    print("=" * 60)
    print("EVAL MODE: Testing trained agent on real network")
    print(f"Model: {model_path}  Episodes: {num_episodes}")
    print("=" * 60)

    env = make_env(simulation=False, num_links=4)
    model = MaskablePPO.load(model_path)
    inner = env.env

    for episode in range(num_episodes):
        if inject_failures:
            link_idx = random.randint(0, inner.num_links - 1)
            port = gns3_port_for_link(link_idx)
            print(f"\n--- Episode {episode + 1}: failing link {link_idx} (port {port}) ---")
            set_link_state(port, down=True)
            time.sleep(2)

        obs, _ = env.reset()
        episode_reward = 0.0

        for step in range(timesteps_per_episode):
            action_masks = env.action_masks()
            action, _ = model.predict(obs, action_masks=action_masks, deterministic=True)
            obs, reward, done, truncated, _ = env.step(action)
            action_label = "no-op" if action == 0 else f"reroute link {action - 1}"
            print(f"  step {step + 1:2d}: {action_label}  reward={reward:+.3f}")
            episode_reward += reward
            if done or truncated:
                break

        if inject_failures:
            set_link_state(port, down=False)
            time.sleep(2)

        print(f"  → Episode reward: {episode_reward:.2f}")
        print(f"  → Network healthy: {'yes' if done else 'no'}")

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
