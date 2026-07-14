import paramiko
import time
import sys
import argparse

GNS3_SSH = {
    "host": "192.168.158.128",
    "user": "gns3",
    "password": "gns3",
    "ovs_container": "GNS3.OpenvSwitch-1.03cc82b5-8a13-43d7-81ed-8e7439855cf8",
    "shell_menu_key": "4",
}


def ssh_and_run(cmds, menu=True):
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

    if menu and b"information" in out.lower() and b"shell" in out.lower():
        print("[menu] selecting 'shell' (key %s)..." % GNS3_SSH["shell_menu_key"])
        chan.send(GNS3_SSH["shell_menu_key"] + "\n")
        time.sleep(1.5)
        while chan.recv_ready():
            chan.recv(4096)
            time.sleep(0.1)

    for cmd in cmds:
        print(f"[cmd] $ {cmd}")
        full = f"docker exec {GNS3_SSH['ovs_container']} {cmd}\n" if not cmd.startswith("docker") else cmd + "\n"
        chan.send(full)
        time.sleep(1.5)
        out = b""
        while chan.recv_ready():
            out += chan.recv(4096)
            time.sleep(0.3)
        text = out.decode(errors="replace")
        try:
            print(text)
        except UnicodeEncodeError:
            print(text.encode(sys.stdout.encoding, errors="replace").decode(sys.stdout.encoding))

    client.close()


def main():
    parser = argparse.ArgumentParser(description="OVS diagnostic tool")
    parser.add_argument("cmd", nargs="?", default=None,
                        help="Custom command to run on the OVS container")
    args = parser.parse_args()

    print("=" * 60)
    print("OVS DIAGNOSTIC")
    print("=" * 60)

    base = [
        "ovs-vsctl show",
        "ovs-ofctl show",
        "ip link show",
    ]
    if args.cmd:
        base.append(args.cmd)

    ssh_and_run(base)

    print("=" * 60)
    print("ODL TOPOLOGY CHECK")
    print("=" * 60)
    try:
        from sdn_client import SDNClient
        odl = SDNClient("http://192.168.158.130:8181")
        if odl.health_check():
            nodes = odl.get_nodes()
            print(f"\nODL nodes ({len(nodes)}):")
            for n in nodes:
                nid = n.get("id", "?")
                connectors = n.get("node-connector", [])
                ports = [c.get("id", "").split(":")[-1] for c in connectors]
                print(f"  {nid}  ports: {', '.join(ports)}")

            topo = odl.get_topology()
            if topo:
                tnodes = topo.get("node", [])
                print(f"\nTopology nodes ({len(tnodes)}):")
                for tn in tnodes:
                    tnid = tn.get("node-id", "?")
                    tps = tn.get("termination-point", [])
                    tp_ports = [tp.get("tp-id", "").split(":")[-1] for tp in tps]
                    print(f"  {tnid}  tp_ports: {', '.join(tp_ports)}")

            links = odl.get_links()
            print(f"\nLinks ({len(links)}):")
            for l in links:
                print(f"  {l['src']['device']}:{l['src']['port']} <-> {l['dst']['device']}:{l['dst']['port']}")

            if not links:
                print("  (no links found - ODL needs LLDP or fallback config)")
                print("\n  To configure, add to train.py:")
                print('  FALLBACK_LINKS = [')
                for n in nodes:
                    nid = n.get("id", "?")
                    connectors = n.get("node-connector", [])
                    for c in connectors:
                        port = c.get("id", "").split(":")[-1]
                        if port in ("1", "2"):  # example ports
                            print(f'      {{"src": {{"device": "{nid}", "port": "{port}"}},')
                            print(f'       "dst": {{"device": "<target-node>", "port": "<target-port>"}}}},')
                print('  ]')
        else:
            print("ODL unreachable at http://192.168.158.130:8181")
    except Exception as e:
        print(f"ODL check failed: {e}")


if __name__ == "__main__":
    main()
