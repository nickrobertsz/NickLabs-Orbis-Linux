#!/usr/bin/env python3
"""Send a payload to GoldHEN's BinLoader on the PS4 (TCP port 9090) over the LAN.

  python3 packaging/scripts/send_payload.py 192.168.1.50 out/references/linux-1024mb.elf

GoldHEN must be running with its BinLoader server enabled (GoldHEN settings). Only private
LAN addresses are accepted, matching the NickLabs rule that consoles stay off the internet.
"""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import socket
import sys
from pathlib import Path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("ps4_ip")
    ap.add_argument("payload")
    ap.add_argument("--port", type=int, default=9090)
    a = ap.parse_args(argv)
    ip = ipaddress.ip_address(a.ps4_ip)
    if not ip.is_private:
        print(f"{ip} is not a private LAN address; refusing", file=sys.stderr)
        return 2
    data = Path(a.payload).read_bytes()
    print(f"sending {a.payload} ({len(data)} bytes, sha256 {hashlib.sha256(data).hexdigest()[:16]}) to {ip}:{a.port}")
    try:
        with socket.create_connection((str(ip), a.port), timeout=10) as s:
            s.sendall(data)
            s.shutdown(socket.SHUT_WR)
    except OSError as exc:
        print(f"failed: {exc}. Is GoldHEN running with BinLoader enabled, and is {ip} the console?", file=sys.stderr)
        return 1
    print("sent. The screen goes black for a few seconds while Linux starts.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
