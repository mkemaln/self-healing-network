# Self-Healing Network – AGENTS.md

## Project overview

Research prototype: **RL-driven self-healing SDN** using GNS3 + OpenDaylight + MaskablePPO.
The RL agent observes network state via the OpenDaylight REST API, decides reroute/QoS actions, and receives a reward based on throughput, latency, jitter, and packet loss.

## Quick start

```bash
source Scripts/activate        # or: .\Scripts\activate
pip install -r requirements.txt
```

## Architecture

```
GNS3 (OVS + MikroTik CRS/CHR)  ←OpenFlow 6653→  ODL VM (VMware, bridged)
                                                    ↕ REST :8181
                                        Python (sdn_client.py → monitor.py → env)
```

**Known infra constraint:** The SDN controller VM *must* use a **Bridged** (or Host-Only) adapter — NAT prevents OVS from initiating the OpenFlow handshake, so devices never appear in the controller.

## Entrypoints

| Script | Purpose |
|--------|---------|
| `model.py` | Prototype: MaskablePPO on **CartPole-v1** with action masking |
| `mountain-car.py` | Prototype: MaskablePPO on **MountainCar-v0** (bug: missing `pandas`/`matplotlib` imports) |
| `test.py` | Load saved `masked_ppo_cartpole.zip` and render |
| `sdn_client.py` | ODL REST API wrapper (nodes, connectors, links, port stats) |
| `monitor.py` | Baseline network monitor — polls ODL, logs metrics to CSV |
| `self_healing_env.py` | Custom Gym environment for self-healing SDN |
| `train.py` | Train MaskablePPO agent (`python train.py sim` or `python train.py real`) |

## Files & ownership

| Area | Files |
|------|-------|
| Exploration (CartPole/MountainCar) | `model.py`, `mountain-car.py`, `test.py`, `masked_ppo_cartpole.zip` |
| SDN integration layer | `sdn_client.py` |
| Monitoring / data collection | `monitor.py` |
| RL environment | `self_healing_env.py` |
| Training script | `train.py` |

## Training pipeline

The RL agent follows Option C architecture: agent decides **when** and **where** to reroute; ODL handles the actual path computation.

```
┌──────────┐   poll ODL    ┌──────────────┐   push flows   ┌─────────┐
│  Agent   │ ────────────→ │  SelfHealing │ ────────────→ │   ODL   │
│  (PPO)   │ ←──────────── │  Env         │ ←──────────── │  REST   │
└──────────┘   obs+reward  └──────────────┘   new stats   └─────────┘
```

**Episode flow:**
1. `env.reset()` → poll ODL → return current network state
2. `env.step(action)`:
   - action=0: do nothing
   - action≥1: reroute traffic away from link `action-1`
   - Wait for convergence, poll new state, compute reward
3. Episode ends when network heals (loss < 1%) or max steps hit

**Reward function (v1):**
```
reward = throughput_change × 2.0
       + loss_reduction × 5.0
       + connectivity_score × 3.0
       - action_penalty (0.1 if rerouting)
```
Throughput/loss use deltas from previous step. Connectivity scores revived vs dead links. Action penalty prevents flapping.

**Training in simulation mode** (no infra needed):
```bash
python train.py sim 50000   # ~5 minutes on CPU
```

**Training on real network:**
```bash
# Configure GNS3_SSH in train.py first
python train.py real
```

Adjust `GNS3_SSH` dict in `train.py` to match your GNS3 VM credentials. SSH is used for failure injection (`ovs-ofctl mod-port ... down/up`).

## Training time estimate

| Mode | Topology | Timesteps | Wall time |
|------|----------|-----------|-----------|
| Simulation | 4 links | 50K | ~5 minutes |
| Real (ODL) | 4 links | 50K | ~28 hours (2s/step) |
| Real (ODL) | 7 links | 100K | ~55 hours |

## Known issues

- `mountain-car.py` uses `pd`/`plt` without importing `pandas`/`matplotlib` — crashes at plotting block.
- `model.py` and `mountain-car.py` both save to `masked_ppo_cartpole.zip` — running one overwrites the other.
- SDN controller VM **must** use a bridged network adapter for OVS to connect.
- OpenDaylight default credentials: `admin:admin` on RESTCONF port **8181**.
- ODL port statistics require the `odl-port-statistics` feature installed.
- `SelfHealingEnv` action space assumes links 0..N-1 match link indices in ODL topology. Adjust `num_links` to match your topology.

## Dependencies

`gymnasium` / `stable-baselines3` / `sb3-contrib` / `requests` / `pandas`. No test framework, no linter, no CI.
