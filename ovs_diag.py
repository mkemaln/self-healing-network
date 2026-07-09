import paramiko
import time
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
        print(text)

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


if __name__ == "__main__":
    main()
