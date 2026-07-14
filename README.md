# Self-Healing SDN with MaskablePPO

RL-driven self-healing network using OpenDaylight, GNS3, and MaskablePPO.

## Prerequisites

| Dependency | Version | Notes |
|-----------|---------|-------|
| Python | 3.9+ | — |
| OpenDayLight | Mg (Sulfur+) | RESTCONF on port 8181 |
| GNS3 VM | Any | Bridged adapter required (NAT breaks OpenFlow) |

## Setup

```bash
python -m venv venv
source venv/Scripts/activate      # Windows
source venv/bin/activate          # Linux/macOS
pip install -r requirements.txt
```

## Training

### Simulation mode (no infra needed)

```bash
python train.py sim          # default 50K timesteps, ~5 min on CPU
python train.py sim 100000   # custom timesteps
```

Model saved to `self_healing_sim.zip`.

### Real mode (GNS3 + ODL required)

Edit `GNS3_SSH` dict in `train.py` to match your GNS3 VM:

```python
GNS3_SSH = {
    "host": "192.168.158.130",    # your GNS3 VM IP
    "user": "admin",
    "bridge": "br0",
}
```

```bash
python train.py real    # 100 episodes × 50 steps ~28 hours for 4 links
```

Ensures ODL VM uses a **bridged** network adapter. Model checkpoints saved every 20 episodes as `self_healing_checkpoint_N.zip`.

## Testing the agent on your lab

Runs a saved model on the real network, injects link failures, and logs the agent's actions.

```bash
python train.py eval                        # uses self_healing_sim.zip
python train.py eval self_healing_final     # pick a different model
```

The agent runs 3 episodes by default. Each episode:
1. Takes a random link down via SSH (`ovs-ofctl mod-port ... down`)
2. Lets the agent observe the degraded metrics and decide reroute actions
3. Restores the link at the end
4. Reports per-step actions and cumulative reward

Sample output:
```
--- Episode 1: failing link 2 (port 3) ---
  step  1: no-op           reward=-2.340
  step  2: reroute link 2  reward=+1.875
  step  3: no-op           reward=+0.520
  ...
  → Episode reward: 4.73
  → Network healthy: yes
```

**Prerequisites for eval:**
- GNS3 lab running with OVS switches connected to ODL
- ODL reachable on `http://localhost:8181`
- OVS bridge name matches `GNS3_SSH["bridge"]` in `train.py`
- SSH access to GNS3 VM configured

## Files

| File | Purpose |
|------|---------|
| `sdn_client.py` | ODL REST API wrapper (`/rests/data/*`) |
| `self_healing_env.py` | Gymnasium environment |
| `train.py` | Entry point (`sim` / `real`) |
| `monitor.py` | Baseline polling + CSV logging |
| `model.py` | CartPole prototype (exploration) |
| `rests_data_result.json` | Reference ODL API response |

## Known issues

- ODL VM adapter must be **bridged** — NAT prevents OpenFlow handshake.
- Port stats embedded in inventory nodes; standalone endpoint returns 404.
- Topology links reconstructed from flow rules when LLDP discovery is unavailable.
- Default ODL credentials: `admin:admin` on port 8181.

## Accessing Karaf Console for ODL
- /opt/opendaylight/bin/client
- do random command

## N.B Information
![](/image/Reward%20Info.png)