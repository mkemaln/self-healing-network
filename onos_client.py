import requests
from requests.auth import HTTPBasicAuth
import time


class ONOSClient:
    def __init__(self, base_url="http://localhost:8181", username="onos", password="rocks"):
        self.base_url = base_url.rstrip("/")
        self.auth = HTTPBasicAuth(username, password)
        self.session = requests.Session()
        self.session.auth = self.auth
        self.session.headers.update({"Accept": "application/json"})

    def _get(self, path, params=None):
        url = f"{self.base_url}/onos/v1{path}"
        try:
            r = self.session.get(url, params=params, timeout=10)
            r.raise_for_status()
            return r.json()
        except requests.exceptions.RequestException as e:
            print(f"[ONOSClient] GET {url} failed: {e}")
            return None

    def get_devices(self):
        data = self._get("/devices")
        return data.get("devices", []) if data else []

    def get_device_ports(self, device_id):
        data = self._get(f"/devices/{device_id}/ports")
        return data.get("ports", []) if data else []

    def get_links(self):
        data = self._get("/links")
        return data.get("links", []) if data else []

    def get_hosts(self):
        data = self._get("/hosts")
        return data.get("hosts", []) if data else []

    def get_topology(self):
        return self._get("/topology")

    def get_port_stats(self, device_id):
        stats = []
        for port_type in ("bytes", "packets"):
            data = self._get(f"/statistics/ports/{device_id}?type={port_type}")
            if data:
                stats.extend(data.get("statistics", []))
        return stats

    def get_all_port_stats(self):
        devices = self.get_devices()
        all_stats = {}
        for dev in devices:
            did = dev["id"]
            all_stats[did] = self.get_port_stats(did)
        return all_stats

    def get_apps(self):
        data = self._get("/applications")
        return data.get("applications", []) if data else []

    def health_check(self):
        try:
            r = self.session.get(
                f"{self.base_url}/onos/v1/topology",
                timeout=5,
            )
            return r.status_code == 200
        except requests.exceptions.RequestException:
            return False


if __name__ == "__main__":
    import sys
    url = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8181"

    client = ONOSClient(url)

    if not client.health_check():
        print(f"Cannot reach ONOS at {url}")
        sys.exit(1)

    print(f"Connected to ONOS at {url}\n")

    devices = client.get_devices()
    print(f"Devices ({len(devices)}):")
    for d in devices:
        ports = client.get_device_ports(d["id"])
        print(f"  {d['id']}  available={d.get('available')}  ports={len(ports)}")

    links = client.get_links()
    print(f"\nLinks ({len(links)}):")
    for l in links:
        print(f"  {l.get('src', '?')} <-> {l.get('dst', '?')}  state={l.get('state', '?')}")

    if devices:
        print(f"\nPort stats for {devices[0]['id']}:")
        stats = client.get_port_stats(devices[0]["id"])
        for s in stats:
            print(f"  port={s.get('port')}  rx_bytes={s.get('bytesReceived')}  tx_bytes={s.get('bytesSent')}")
