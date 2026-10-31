#!/usr/bin/env python3
"""Config drift detection: intended config (from Jinja) vs running config (on the router)."""
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SOT = ROOT / "ansible/group_vars/all.yml"
TOPO = ROOT / "topology.clab.yml"
G, R, B, X = "\033[32m", "\033[31m", "\033[1m", "\033[0m"

# Lines FRR adds or prints on its own; they are not part of our design
IGNORE = ("!", "exit", "exit-address-family", "end", "Building configuration...",
          "Current configuration:", "frr version", "domainname", "no ipv6 forwarding")


def to_lines(text):
    """Turn a config into a set like {'interface eth1 > ip ospf area 0', ...}.

    Each line keeps the block it belongs to, so the order of blocks
    in the file does not matter, only the content.
    """
    result, parents = set(), []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(IGNORE):
            continue
        depth = len(raw) - len(raw.lstrip(" "))
        parents = parents[:depth]
        result.add(" > ".join(parents + [line]))
        parents.append(line)
    return result


def running_config(lab, node):
    cmd = ["docker", "exec", f"clab-{lab}-{node}", "vtysh", "-c", "show running-config"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else None


def main():
    lab = yaml.safe_load(TOPO.read_text())["name"]
    routers = yaml.safe_load(SOT.read_text())["network"]
    drifted = 0

    print(f"\n{B}Config drift check{X}  (intended = configs/<router>/frr.conf)\n")
    for node in routers:
        actual_text = running_config(lab, node)
        if actual_text is None:
            print(f"{node:<4} {R}UNREACHABLE{X}")
            drifted += 1
            continue

        intended = to_lines((ROOT / "configs" / node / "frr.conf").read_text())
        actual = to_lines(actual_text)
        missing = sorted(intended - actual)   # should be there, but is not
        extra = sorted(actual - intended)     # is there, but should not be

        if not missing and not extra:
            print(f"{node:<4} {G}NO DRIFT{X}")
            continue

        drifted += 1
        print(f"{node:<4} {R}DRIFT{X}")
        for line in missing:
            print(f"       {R}- missing: {line}{X}")
        for line in extra:
            print(f"       {G}+ extra:   {line}{X}")

    if drifted:
        print(f"\nOverall: {R}{drifted} router(s) drifted{X}\n")
        sys.exit(1)
    print(f"\nOverall: {G}IN SYNC{X}\n")


if __name__ == "__main__":
    main()
