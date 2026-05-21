# Self-Healing Network – AGENTS.md

## Project overview

Research prototype: **RL-driven self-healing SDN** using GNS3 + ONOS + MaskablePPO.
The RL agent observes network state via the ONOS REST API, decides reroute/QoS actions, and receives a reward based on throughput, latency, jitter, and packet loss.

## Quick start

```bash
source Scripts/activate        # or: .\Scripts\activate
pip install -r requirements.txt
```

## Architecture

```
GNS3 (OVS + MikroTik CRS/CHR)  ←OpenFlow 6653→  ONOS VM (VMware, bridged)
                                                    ↕ REST :8181
                                        Python (onos_client.py → monitor.py → env)
```

**Known infra constraint:** The ONOS VM *must* use a **Bridged** (or Host-Only) adapter — NAT prevents OVS from initiating the OpenFlow handshake, so devices never appear in ONOS.

## Entrypoints

| Script | Purpose |
|--------|---------|
| `model.py` | Prototype: MaskablePPO on **CartPole-v1** with action masking |
| `mountain-car.py` | Prototype: MaskablePPO on **MountainCar-v0** (bug: missing `pandas`/`matplotlib` imports) |
| `test.py` | Load saved `masked_ppo_cartpole.zip` and render |
| `onos_client.py` | ONOS REST API wrapper (devices, ports, links, stats) |
| `monitor.py` | Baseline network monitor — polls ONOS, logs metrics to CSV |

## Files & ownership

| Area | Files |
|------|-------|
| Exploration (CartPole/MountainCar) | `model.py`, `mountain-car.py`, `test.py`, `masked_ppo_cartpole.zip` |
| ONOS integration layer | `onos_client.py` |
| Monitoring / data collection | `monitor.py` |
| RL environment (coming soon) | `self_healing_env.py` |
| Training script (coming soon) | `train.py` |

## Known issues

- `mountain-car.py` uses `pd`/`plt` without importing `pandas`/`matplotlib` — crashes at plotting block.
- `model.py` and `mountain-car.py` both save to `masked_ppo_cartpole.zip` — running one overwrites the other.
- ONOS VM **must** use a bridged network adapter for OVS to connect.

## Dependencies

`gymnasium` / `stable-baselines3` / `sb3-contrib` / `requests` / `pandas`. No test framework, no linter, no CI.
