import requests
from requests.auth import HTTPBasicAuth


class SDNClient:
    def __init__(self, base_url="http://localhost:8181", username="admin", password="admin"):
        self.base_url = base_url.rstrip("/")
        self.auth = HTTPBasicAuth(username, password)
        self.session = requests.Session()
        self.session.auth = self.auth
        self.session.headers.update({"Accept": "application/json"})

    """
    @agents there need some changes on _get on link restconf/operational, presumably it instead used rests/data
    """ 
    def _get(self, path, params=None):
        url = f"{self.base_url}/rests/data{path}" 
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
        return data.get("opendaylight-inventory:nodes", {}).get("node", [])

    """
    @agents there need some changes getting the node id, because the structure i got from opendaylight-inventory:nodes are like this 
    (some snippet code)

    "opendaylight-inventory:nodes": {
    "node": [
      {
        "id": "openflow:1407374883553280",
        "flow-node-inventory:table": [
          {
            "id": 0,
            "flow": [
              {
                "id": "1001",
                "table_id": 0,
                "priority": 100,
                "flow-name": "test-in-ether1-out-ether3",
                "idle-timeout": 0,
                "match": {
                  "in-port": "openflow:1407374883553280:1"
                },
                "instructions": {
                  "instruction": [
                    {
                      "order": 0,
                      "apply-actions": {
                        "action": [
                          {
                            "order": 0,
                            "output-action": {
                              "max-length": 65535,
                              "output-node-connector": "3"
                            }
                          }
                        ]
                      }
                    }
                  ]
                },
                "hard-timeout": 0
              }
            ]
          }
        ],
        "flow-node-inventory:snapshot-gathering-status-start": {
          "begin": "2026-06-29T02:36:25.215Z"
        },
        "node-connector": [
          {
            "id": "openflow:1407374883553280:3",
            "flow-node-inventory:port-number": 3,
            "flow-node-inventory:hardware-address": "0c:b7:4c:57:00:02",
            "flow-node-inventory:peer-features": "",
            "flow-node-inventory:name": "ether3",
            "flow-node-inventory:current-feature": "",
            "flow-node-inventory:supported": "",
            "flow-node-inventory:advertised-features": "",
            "flow-node-inventory:state": {
              "live": false,
              "blocked": false,
              "link-down": false
            },
    
    which you could see its different from the proposed get_node_connector
    """ 
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
        if topo and "link" in topo:
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
            if links:
                return links
        return self._build_links_from_flows()

    def _build_links_from_flows(self):
        nodes = self.get_nodes()
        links = []
        link_id = 0
        for node in nodes:
            node_id = node.get("id")
            tables = node.get("flow-node-inventory:table", [])
            for table in tables:
                for flow in table.get("flow", []):
                    match = flow.get("match", {})
                    in_port = match.get("in-port", "")
                    flow_name = flow.get("flow-name", f"flow-{link_id}")
                    instructions = flow.get("instructions", {}).get("instruction", [])
                    for inst in instructions:
                        actions = inst.get("apply-actions", {}).get("action", [])
                        for action in actions:
                            output = action.get("output-action", {})
                            out_port = output.get("output-node-connector", "")
                            if in_port and out_port:
                                in_port_num = in_port.split(":")[-1] if ":" in in_port else in_port
                                links.append({
                                    "link-id": flow_name,
                                    "src": {"device": node_id, "port": in_port_num},
                                    "dst": {"device": node_id, "port": out_port},
                                })
                                link_id += 1
        return links

    def get_all_port_stats(self):
        data = self._get("/opendaylight-inventory:nodes")
        if not data:
            return {}
        nodes = data.get("opendaylight-inventory:nodes", {}).get("node", [])
        result = {}
        for n in nodes:
            node_id = n.get("id")
            if not node_id:
                continue
            connectors = n.get("node-connector", [])
            stats_list = []
            for c in connectors:
                tp_id = c.get("id", "")
                port = tp_id.split(":")[-1] if ":" in tp_id else tp_id
                stats = c.get(
                    "opendaylight-port-statistics:flow-capable-node-connector-statistics",
                    {},
                )
                byte_stats = stats.get("bytes", {})
                pkt_stats = stats.get("packets", {})
                stats_list.append({
                    "port": port,
                    "bytesReceived": int(byte_stats.get("received", 0)),
                    "bytesSent": int(byte_stats.get("transmitted", 0)),
                    "packetsReceived": int(pkt_stats.get("received", 0)),
                    "packetsSent": int(pkt_stats.get("transmitted", 0)),
                })
            result[node_id] = stats_list
        return result

    def get_all_stats(self):
        return self.get_all_port_stats()

    def push_flow(self, node_id, flow_id, in_port, out_port, priority=100):
        """Install a flow rule that redirects in_port traffic to out_port."""
        url = (f"{self.base_url}/rests/data/opendaylight-inventory:nodes/"
               f"node/{requests.utils.quote(node_id, safe='')}/table/0/"
               f"flow/{flow_id}")
        payload = {
            "flow-node-inventory:flow": [{
                "id": str(flow_id),
                "table_id": 0,
                "priority": priority,
                "hard-timeout": 0,
                "idle-timeout": 0,
                "match": {
                    "in-port": f"{node_id}:{in_port}",
                },
                "instructions": {
                    "instruction": [{
                        "order": 0,
                        "apply-actions": {
                            "action": [{
                                "order": 0,
                                "output-action": {
                                    "output-node-connector": str(out_port),
                                },
                            }],
                        },
                    }],
                },
            }],
        }
        try:
            r = self.session.put(url, json=payload, timeout=10)
            if r.status_code in (200, 201, 204):
                return True
            print(f"[SDNClient] push_flow status={r.status_code}: {r.text[:200]}")
            return False
        except requests.exceptions.RequestException as e:
            print(f"[SDNClient] push_flow failed: {e}")
            return False

    def delete_flow(self, node_id, flow_id):
        """Remove a flow rule."""
        url = (f"{self.base_url}/rests/data/opendaylight-inventory:nodes/"
               f"node/{requests.utils.quote(node_id, safe='')}/table/0/"
               f"flow/{flow_id}")
        try:
            r = self.session.delete(url, timeout=10)
            return r.status_code in (200, 204)
        except requests.exceptions.RequestException:
            return False

    def health_check(self):
        try:
            r = self.session.get(
                f"{self.base_url}/rests/data/network-topology:network-topology",
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
