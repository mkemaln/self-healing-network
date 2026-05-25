import requests
from requests.auth import HTTPBasicAuth


class SDNClient:
    def __init__(self, base_url="http://localhost:8181", username="admin", password="admin"):
        self.base_url = base_url.rstrip("/")
        self.auth = HTTPBasicAuth(username, password)
        self.session = requests.Session()
        self.session.auth = self.auth
        self.session.headers.update({"Accept": "application/json"})

    def _get(self, path, params=None):
        url = f"{self.base_url}/restconf/operational{path}"
        try:
            r = self.session.get(url, params=params, timeout=10)
            r.raise_for_status()
            return r.json()
        except requests.exceptions.RequestException as e:
            print(f"[SDNClient] GET {url} failed: {e}")
            return None

    def get_nodes(self):
        data = self._get("/opendaylight-inventory:nodes")
        if not data:
            return []
        return data.get("nodes", {}).get("node", [])

    def get_node_connectors(self, node_id):
        nodes = self.get_nodes()
        for n in nodes:
            if n.get("id") == node_id or n.get("node-id") == node_id:
                return n.get("node-connector", [])
        return []

    def get_topology(self):
        data = self._get("/network-topology:network-topology")
        if not data:
            return None
        topos = data.get("network-topology", {}).get("topology", [])
        return topos[0] if topos else None

    def get_links(self):
        topo = self.get_topology()
        if not topo:
            return []
        raw = topo.get("link", [])
        links = []
        for l in raw:
            src = l.get("source", {})
            dst = l.get("destination", {})
            links.append({
                "link-id": l.get("link-id"),
                "src": {
                    "device": src.get("source-node"),
                    "port": src.get("source-tp"),
                },
                "dst": {
                    "device": dst.get("dest-node"),
                    "port": dst.get("dest-tp"),
                },
            })
        return links

    def get_all_port_stats(self):
        data = self._get("/opendaylight-port-statistics:port-statistics")
        if not data:
            return {}
        raw = data.get("port-statistics", [])
        result = {}
        for entry in raw:
            node_id = entry.get("node", {}).get("id")
            if not node_id:
                continue
            connectors = entry.get("node-connector-statistics", [])
            stats_list = []
            for c in connectors:
                nc = c.get("node-connector", {})
                tp_id = nc.get("id", "")
                port = tp_id.split(":")[-1] if ":" in tp_id else tp_id
                byte_stats = c.get("bytes", {})
                pkt_stats = c.get("packets", {})
                stats_list.append({
                    "port": port,
                    "bytesReceived": int(byte_stats.get("receive", 0)),
                    "bytesSent": int(byte_stats.get("transmit", 0)),
                    "packetsReceived": int(pkt_stats.get("receive", 0)),
                    "packetsSent": int(pkt_stats.get("transmit", 0)),
                })
            result[node_id] = stats_list
        return result

    def get_all_stats(self):
        return self.get_all_port_stats()

    def health_check(self):
        try:
            r = self.session.get(
                f"{self.base_url}/restconf/operational/network-topology:network-topology",
                timeout=5,
            )
            return r.status_code == 200
        except requests.exceptions.RequestException:
            return False


if __name__ == "__main__":
    import sys
    url = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8181"

    client = SDNClient(url)

    if not client.health_check():
        print(f"Cannot reach OpenDaylight at {url}")
        sys.exit(1)

    print(f"Connected to OpenDaylight at {url}\n")

    nodes = client.get_nodes()
    print(f"Nodes ({len(nodes)}):")
    for n in nodes:
        nid = n.get("id", n.get("node-id", "?"))
        connectors = n.get("node-connector", [])
        print(f"  {nid}  connectors={len(connectors)}")

    links = client.get_links()
    print(f"\nLinks ({len(links)}):")
    for l in links:
        s = l["src"]
        d = l["dst"]
        print(f"  {s['device']}:{s['port']} <-> {d['device']}:{d['port']}")

    if nodes:
        first_id = nodes[0].get("id", nodes[0].get("node-id", ""))
        print(f"\nPort stats for {first_id}:")
        stats = client.get_all_port_stats()
        for entry in stats.get(first_id, []):
            print(f"  port={entry['port']}  rx={entry['bytesReceived']}  tx={entry['bytesSent']}")
