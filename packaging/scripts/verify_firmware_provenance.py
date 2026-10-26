#!/usr/bin/env python3
"""Compare operator-supplied firmware with the copies shipped by Arch's linux-firmware packages.

  python3 packaging/scripts/verify_firmware_provenance.py --rootfs out/rootfs/dir \\
      --initramfs-manifest out/initramfs/initramfs-cuh-1215a.cpio.gz.manifest.json \\
      -o out/release/firmware-provenance.json

For each firmware file in the initramfs build manifest, look for the same path under
<rootfs>/usr/lib/firmware (plain, .zst or .xz, as Arch installs them). If the decompressed
bytes are identical, the file is the linux-firmware one, which records its licence in
WHENCE (e.g. LICENCE.Marvell). This tool only REPORTS; it never edits manifests/firmware.json.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import lzma
import shutil
import subprocess
import sys
from pathlib import Path


def decompressed(p: Path) -> bytes | None:
    if p.suffix == ".xz":
        return lzma.decompress(p.read_bytes())
    if p.suffix == ".zst":
        z = shutil.which("zstd")
        if not z:
            return None
        return subprocess.run([z, "-dc", str(p)], capture_output=True, check=True).stdout
    return p.read_bytes()


def package_versions(rootfs: Path) -> dict[str, str]:
    f = rootfs / "usr/share/orbis/packages.txt"
    out = {}
    if f.is_file():
        for line in f.read_text().splitlines():
            parts = line.split()
            if len(parts) == 2:
                out[parts[0]] = parts[1]
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--rootfs", required=True)
    ap.add_argument("--initramfs-manifest", required=True)
    ap.add_argument("-o", "--output")
    a = ap.parse_args(argv)
    root = Path(a.rootfs)
    man = json.loads(Path(a.initramfs_manifest).read_text())
    vers = package_versions(root)
    rows = []
    for f in man.get("firmware", []):
        base = root / "usr/lib/firmware" / f["path"]
        cands = [base, base.with_name(base.name + ".zst"), base.with_name(base.name + ".xz")]
        found = next((c for c in cands if c.is_file()), None)
        row = {"path": f["path"], "group": f["group"], "initramfs_sha256": f["sha256"]}
        if not found:
            row.update(result="not in rootfs linux-firmware", arch_sha256=None)
        else:
            data = decompressed(found)
            if data is None:
                row.update(result="could not decompress (zstd missing)", arch_file=str(found.relative_to(root)))
            else:
                h = hashlib.sha256(data).hexdigest()
                row.update(arch_file=str(found.relative_to(root)), arch_sha256=h,
                           result="identical to Arch linux-firmware" if h == f["sha256"] else "DIFFERENT from Arch linux-firmware")
        rows.append(row)
    lf = {k: v for k, v in vers.items() if k.startswith("linux-firmware")}
    report = {"schema": 1, "linux_firmware_packages": lf, "files": rows,
              "note": "identical files carry the licence recorded for them in linux-firmware WHENCE; "
                      "files not found there keep their manifests/firmware.json status"}
    text = json.dumps(report, indent=1)
    if a.output:
        Path(a.output).parent.mkdir(parents=True, exist_ok=True)
        Path(a.output).write_text(text + "\n")
    print(text)
    for r in rows:
        print(f"{r['result']:<36} {r['path']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
