#!/usr/bin/env python3
"""Download the community reference inputs listed in manifests/references.json and verify them.

  python3 packaging/scripts/fetch_references.py [--out out/references] [--only community-initramfs]

Every file must match its recorded SHA-256; a mismatch is deleted and reported, never used.
Already-present files with the right hash are not downloaded again. These files are inputs
for the operator's own build (firmware source, loader payload, recovery kernel); Orbis never
redistributes them.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch(url: str, dest: Path) -> None:
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "nicklabs-orbis-fetch/0.1"})
    with urllib.request.urlopen(req, timeout=120) as r, open(tmp, "wb") as f:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
    tmp.replace(dest)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--manifest", default=str(REPO / "manifests/references.json"))
    ap.add_argument("--out", default=str(REPO / "out/references"))
    ap.add_argument("--only", action="append", help="artifact name (repeatable)")
    a = ap.parse_args(argv)
    refs = json.loads(Path(a.manifest).read_text())["artifacts"]
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    bad = 0
    for ref in refs:
        if a.only and ref["name"] not in a.only:
            continue
        dest = out / ref["file"]
        if dest.exists() and sha256(dest) == ref["sha256"]:
            print(f"ok (cached)  {ref['file']}")
            continue
        print(f"fetch        {ref['url']}")
        try:
            fetch(ref["url"], dest)
        except OSError as exc:
            print(f"FAILED       {ref['file']}: {exc}", file=sys.stderr)
            bad += 1
            continue
        got = sha256(dest)
        if got != ref["sha256"]:
            dest.unlink()
            print(f"MISMATCH     {ref['file']}: got {got}, expected {ref['sha256']} (deleted)", file=sys.stderr)
            bad += 1
            continue
        print(f"ok           {ref['file']} {got[:16]}")
    (out / "README.txt").write_text(
        "Community reference inputs fetched by NickLabs Orbis (manifests/references.json).\n"
        "Verified by SHA-256. Not part of Orbis; do not redistribute.\n")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
