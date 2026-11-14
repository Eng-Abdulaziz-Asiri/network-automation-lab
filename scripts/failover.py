#!/usr/bin/env python3
"""BFD failover test: break the Site A <-> ISP link *silently* and time how fast r1 notices.

A silent failure means the interface stays UP but packets stop flowing
(think: a broken switch or fiber in the middle of the link).
Without BFD, BGP only notices when its hold timer expires (180 s here).
With BFD, the session goes down in well under a second.
"""
import ipaddress
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SOT = ROOT / "ansible/group_vars/all.yml"
TOPO = ROOT / "topology.clab.yml"
G, R, B, X = "\033[32m", "\033[31m", "\033[1m", "\033[0m"

WATCH = "r1"          # router that must detect the failure (its first BGP neighbor is used)
MAX_DETECT = 2.0      # seconds; slower than this means BFD is not doing its job
MAX_RECOVER = 60      # seconds to wait for BGP to come back after the link is fixed


def sh(cmd, check=False):
    return subprocess.run(cmd, capture_output=True, text=True, check=check).stdout.strip()


def vtysh(lab, node, command):
    out = sh(["docker", "exec", f"clab-{lab}-{node}", "vtysh", "-c", command])
    try:
        return json.loads(out) if out else {}
    except json.JSONDecodeError:
        return {}


def bgp_state(lab, node, peer):
    peers = vtysh(lab, node, "show bgp summary json").get("ipv4Unicast", {}).get("peers", {})
    return peers.get(peer, {}).get("state", "Unknown")


def bfd_peer(lab, node, peer):
    return next((p for p in vtysh(lab, node, "show bfd peers json") or []
                 if p.get("peer") == peer), {})


def bfd_state(lab, node, peer):
    return bfd_peer(lab, node, peer).get("status", "none")


def seconds(value):
    return f"{value:.2f} s" if value is not None else f"{R}not detected{X}"


def find_iface(network, ip):
    """Return (router, interface) that sits on the same subnet as `ip` / owns `ip`."""
    owner, link = None, None
    for router, d in network.items():
        for i in d["interfaces"]:
            iface = ipaddress.ip_interface(i["ip"])
            if str(iface.ip) == ip:
                owner = (router, i["name"])
            elif ipaddress.ip_address(ip) in iface.network:
                link = (router, i["name"])
    return owner, link


def drop_traffic(lab, node, iface, enable):
    """Drop every packet in/out of `iface` inside the node's network namespace.

    The link itself stays UP, so only a keepalive protocol (BFD / BGP hold timer)
    can notice that something is wrong.
    """
    pid = sh(["docker", "inspect", "-f", "{{.State.Pid}}", f"clab-{lab}-{node}"])
    action = "-I" if enable else "-D"
    rules = (f"iptables {action} INPUT -i {iface} -j DROP",
             f"iptables {action} OUTPUT -o {iface} -j DROP")
    sudo = [] if os.geteuid() == 0 else ["sudo"]
    # both rules in one call, so they take effect at (almost) the same moment
    subprocess.run(sudo + ["nsenter", "-t", pid, "-n", "sh", "-c",
                           (" && " if enable else "; ").join(rules)], check=enable)


def main():
    lab = yaml.safe_load(TOPO.read_text())["name"]
    network = yaml.safe_load(SOT.read_text())["network"]
    peer = network[WATCH]["bgp"]["neighbors"][0]["ip"]
    (far, far_iface), (_, near_iface) = find_iface(network, peer)

    print(f"\n{B}BFD failover test{X}  ({WATCH} {near_iface} <-> {far} {far_iface}, "
          f"BGP peer {peer})\n")

    if bgp_state(lab, WATCH, peer) != "Established" or bfd_state(lab, WATCH, peer) != "up":
        print(f"{R}Lab is not healthy (BGP/BFD down). Run 'make test' first.{X}\n")
        sys.exit(1)

    hold = vtysh(lab, WATCH, f"show bgp neighbors {peer} json").get(peer, {}) \
        .get("bgpTimerHoldTimeMsecs", 180000) / 1000
    timers = bfd_peer(lab, WATCH, peer)

    if os.geteuid() != 0:
        # ask for the sudo password now, so typing it is not counted in the timing
        subprocess.run(["sudo", "-v"], check=True)

    print(f"  Breaking the link silently: dropping all packets on {far} {far_iface} ...")
    detected, start = None, time.monotonic()
    try:
        drop_traffic(lab, far, far_iface, True)
        while time.monotonic() - start < MAX_DETECT * 5:
            if bgp_state(lab, WATCH, peer) != "Established":
                detected = time.monotonic() - start
                break
            time.sleep(0.05)
        flags = sh(["docker", "exec", f"clab-{lab}-{WATCH}", "ip", "link", "show", near_iface])
        link = "up" if "LOWER_UP" in flags else "down"
    finally:
        drop_traffic(lab, far, far_iface, False)

    print("  Repairing the link, waiting for BGP to come back ...")
    recovered, start = None, time.monotonic()
    while time.monotonic() - start < MAX_RECOVER:
        if bgp_state(lab, WATCH, peer) == "Established" and bfd_state(lab, WATCH, peer) == "up":
            recovered = time.monotonic() - start
            break
        time.sleep(0.5)

    ok = detected is not None and detected <= MAX_DETECT and recovered is not None
    print(f"""
  {WATCH} interface {near_iface} during failure: {link.upper()}  (silent failure)
  BFD timers:                    {timers.get('transmit-interval')} ms x {timers.get('detect-multiplier')}
  Detected with BFD in:          {B}{seconds(detected)}{X}
  Without BFD (BGP hold timer):  {hold:.0f} s
  Recovered in:                  {seconds(recovered)}

{B}Result:{X} {f'{G}PASS{X}' if ok else f'{R}FAIL{X}'}
""")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
