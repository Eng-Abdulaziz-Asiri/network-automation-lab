#!/usr/bin/env python3
"""Lab summary: every device in the lab with live OSPF/BGP status."""
import json
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SOT = ROOT / "ansible/group_vars/all.yml"
TOPO = ROOT / "topology.clab.yml"
G, R, B, X = "\033[32m", "\033[31m", "\033[1m", "\033[0m"


def sh(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


def vtysh(lab, node, command):
    out = sh(["docker", "exec", f"clab-{lab}-{node}", "vtysh", "-c", command])
    try:
        return json.loads(out) if out else {}
    except json.JSONDecodeError:
        return {}


def running(lab, node):
    cmd = ["docker", "inspect", "-f", "{{.State.Running}}", f"clab-{lab}-{node}"]
    return sh(cmd) == "true"


def role(d):
    if "bgp" in d and "ospf" in d:
        return "Edge"
    return "Access" if "ospf" in d else "ISP"


def mark(ok, good="PASS", bad="FAIL"):
    return f"{G}{good}{X}" if ok else f"{R}{bad}{X}"


def main():
    topo = yaml.safe_load(TOPO.read_text())
    lab, nodes, links = topo["name"], topo["topology"]["nodes"], topo["topology"]["links"]
    network = yaml.safe_load(SOT.read_text())["network"]
    hosts = [n for n in nodes if n not in network]

    print(f"\n{B}Lab: {lab}{X}  |  Nodes: {len(nodes)} "
          f"({len(network)} routers, {len(hosts)} hosts)  |  Links: {len(links)}\n")
    head = f"{'Router':<7}{'Role':<8}{'Loopback':<15}{'ASN':<7}{'Ifaces':<7}{'OSPF':<7}{'BGP':<7}{'Routes':<8}Status"
    print(B + head + X)
    print("-" * len(head))

    all_ok = True
    for name, d in network.items():
        asn = str(d.get("bgp", {}).get("asn", "-"))
        base = f"{name:<7}{role(d):<8}{d['loopback']:<15}{asn:<7}{len(d['interfaces']):<7}"
        if not running(lab, name):
            print(base + f"{'-':<7}{'-':<7}{'-':<8}" + mark(False, bad="DOWN"))
            all_ok = False
            continue

        # Expected (from Source of Truth)
        passive = d.get("ospf", {}).get("passive", [])
        exp_ospf = sum(1 for i in d["interfaces"] if i.get("ospf") and i["name"] not in passive)
        exp_bgp = len(d.get("bgp", {}).get("neighbors", []))

        # Live (from the router)
        ospf = vtysh(lab, name, "show ip ospf neighbor json").get("neighbors", {})
        ospf_up = sum(1 for nbrs in ospf.values() for n in nbrs
                      if str(n.get("nbrState", n.get("state", ""))).startswith("Full"))
        peers = vtysh(lab, name, "show bgp summary json").get("ipv4Unicast", {}).get("peers", {})
        bgp_up = sum(1 for p in peers.values() if p.get("state") == "Established")
        routes = len(vtysh(lab, name, "show ip route json"))

        ok = ospf_up == exp_ospf and bgp_up == exp_bgp
        all_ok &= ok
        print(base + f"{f'{ospf_up}/{exp_ospf}':<7}{f'{bgp_up}/{exp_bgp}':<7}{routes:<8}" + mark(ok))

    print(f"\n{B}Hosts{X}")
    ips = {}
    for h in hosts:
        ip = sh(["docker", "exec", f"clab-{lab}-{h}", "sh", "-c",
                 "ip -4 -o addr show eth1 | awk '{print $4}'"])
        ips[h] = ip
        print(f"  {h:<8}{ip or mark(False, bad='DOWN')}")

    if len(hosts) >= 2 and ips.get(hosts[1]):
        src, dst = hosts[0], hosts[1]
        target = ips[dst].split("/")[0]
        ping = subprocess.run(["docker", "exec", f"clab-{lab}-{src}", "ping", "-c", "2", "-W", "1", target],
                              capture_output=True)
        ok = ping.returncode == 0
        all_ok &= ok
        print(f"\n{B}End-to-end:{X} {src} -> {dst} ({target})  {mark(ok)}")

    print(f"\n{B}Overall:{X} {mark(all_ok, 'HEALTHY', 'ISSUES FOUND')}\n")
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
