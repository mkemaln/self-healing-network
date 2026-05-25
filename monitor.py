import time
import csv
import sys
from datetime import datetime, timezone
from collections import defaultdict
from sdn_client import SDNClient


class NetworkMonitor:
    def __init__(self, sdn_url="http://localhost:8181", poll_interval=5):
        self.client = SDNClient(sdn_url)
        self.interval = poll_interval
        self.prev_stats = {}
        self.prev_time = None

    def poll(self):
        nodes = self.client.get_nodes()
        links = self.client.get_links()
        port_stats = self.client.get_all_port_stats()
        now = time.time()

        node_map = {n.get("id", n.get("node-id")): n for n in nodes}

        rows = []
        for link in links:
            src = link.get("src", {})
            dst = link.get("dst", {})
            src_device = src.get("device")
            src_port = src.get("port")
            dst_device = dst.get("device")
            dst_port = dst.get("port")

            src_key = f"{src_device}/{src_port}"
            dst_key = f"{dst_device}/{dst_port}"
            key = f"{src_key}-{dst_key}"

            src_stats = self._find_port_stat(port_stats, src_device, src_port)
            dst_stats = self._find_port_stat(port_stats, dst_device, dst_port)

            prev = self.prev_stats.get(key, {})
            dt = (now - self.prev_time) if self.prev_time else self.interval

            row = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "link": key,
                "src_device": src_device,
                "src_port": src_port,
                "dst_device": dst_device,
                "dst_port": dst_port,
                "src_available": src_device in node_map,
                "dst_available": dst_device in node_map,
            }

            if src_stats and dst_stats:
                row.update(self._compute_metrics(
                    src_stats, dst_stats, prev, dt
                ))

            rows.append(row)
            self.prev_stats[key] = {
                "src": src_stats,
                "dst": dst_stats,
            }

        self.prev_time = now
        return rows

    def _find_port_stat(self, port_stats, device_id, port):
        if device_id not in port_stats:
            return None
        for entry in port_stats[device_id]:
            if entry.get("port") == str(port):
                return entry
        return None

    def _compute_metrics(self, src, dst, prev, dt):
        m = {}

        src_rx_bytes = int(src.get("bytesReceived", 0))
        src_tx_bytes = int(src.get("bytesSent", 0))
        dst_rx_bytes = int(dst.get("bytesReceived", 0))
        dst_tx_bytes = int(dst.get("bytesSent", 0))

        src_rx_pkts = int(src.get("packetsReceived", 0))
        src_tx_pkts = int(src.get("packetsSent", 0))
        dst_rx_pkts = int(dst.get("packetsReceived", 0))
        dst_tx_pkts = int(dst.get("packetsSent", 0))

        m["throughput_rx_bps"] = 0.0
        m["throughput_tx_bps"] = 0.0

        if dt > 0 and prev:
            prev_src = prev.get("src", {})
            prev_dst = prev.get("dst", {})

            if prev_src and prev_dst:
                prev_src_rx = int(prev_src.get("bytesReceived", 0))
                prev_src_tx = int(prev_src.get("bytesSent", 0))
                prev_dst_rx = int(prev_dst.get("bytesReceived", 0))
                prev_dst_tx = int(prev_dst.get("bytesSent", 0))

                prev_src_rx_p = int(prev_src.get("packetsReceived", 0))
                prev_src_tx_p = int(prev_src.get("packetsSent", 0))
                prev_dst_rx_p = int(prev_dst.get("packetsReceived", 0))
                prev_dst_tx_p = int(prev_dst.get("packetsSent", 0))

                delta_rx = max(0, dst_rx_bytes - prev_dst_rx)
                delta_tx = max(0, src_tx_bytes - prev_src_tx)
                m["throughput_rx_bps"] = (delta_rx * 8) / dt
                m["throughput_tx_bps"] = (delta_tx * 8) / dt

                tx_pkts = max(0, src_tx_pkts - prev_src_tx_p)
                rx_pkts = max(0, dst_rx_pkts - prev_dst_rx_p)
                if tx_pkts > 0:
                    m["packet_loss_ratio"] = max(0, tx_pkts - rx_pkts) / tx_pkts
                else:
                    m["packet_loss_ratio"] = 0.0

                m["tx_packets_delta"] = tx_pkts
                m["rx_packets_delta"] = rx_pkts

        return m

    def run_loop(self, csv_path="network_baseline.csv", iterations=None):
        fieldnames = [
            "timestamp", "link",
            "src_device", "src_port", "dst_device", "dst_port",
            "src_available", "dst_available",
            "throughput_rx_bps", "throughput_tx_bps",
            "packet_loss_ratio", "tx_packets_delta", "rx_packets_delta",
        ]

        with open(csv_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()

            i = 0
            while iterations is None or i < iterations:
                rows = self.poll()
                for row in rows:
                    writer.writerow(row)
                    print(self._fmt_row(row))

                f.flush()
                i += 1
                time.sleep(self.interval)

        print(f"\nBaseline saved to {csv_path}")

    def _fmt_row(self, row):
        t = row["timestamp"][11:19]
        link = row["link"]
        rx = row.get("throughput_rx_bps", 0)
        tx = row.get("throughput_tx_bps", 0)
        loss = row.get("packet_loss_ratio", -1)
        sa = "U" if row.get("src_available") else "D"
        da = "U" if row.get("dst_available") else "D"
        loss_pct = f"{loss*100:.2f}%" if loss >= 0 else "N/A"
        return (f"[{t}] {link}  RX={rx/1e6:.1f}Mbps  TX={tx/1e6:.1f}Mbps  "
                f"loss={loss_pct}  status={sa}-{da}")


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8181"
    interval = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    out = sys.argv[3] if len(sys.argv) > 3 else "network_baseline.csv"

    mon = NetworkMonitor(url, interval)
    print(f"Monitoring {url} every {interval}s → {out}")
    print("Press Ctrl+C to stop\n")

    try:
        mon.run_loop(out)
    except KeyboardInterrupt:
        print("\nStopped.")
