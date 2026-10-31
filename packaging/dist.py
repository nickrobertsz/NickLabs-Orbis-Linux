#!/usr/bin/env python3
"""Deterministic source tarball of the actual build tree, including local fixes.

This release is built from a separate staging checkout, not the frozen Core
checkout. Git's HEAD or tracked-only files can omit the staged repair helper.
"""
import argparse
import gzip
import io
import hashlib
import json
import subprocess
import tarfile
from pathlib import Path

R = Path(__file__).resolve().parents[1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", required=True)
    ap.add_argument("--out", default=str(R / "out"))
    a = ap.parse_args()
    tracked = subprocess.run(["git", "-C", str(R), "ls-files", "--cached", "--others",
                              "--exclude-standard", "-z"], capture_output=True, check=True).stdout.split(b"\0")
    files = []
    for raw in tracked:
        if not raw:
            continue
        f = raw.decode()
        parts = Path(f).parts
        if any(x in {".git", "out", "__pycache__", "target"} for x in parts):
            continue
        if f.startswith("graphics/") and len(parts) >= 3 and parts[2] in ("src", "pkg"):
            continue
        if f.endswith((".pkg.tar.zst", ".log", ".pyc", ".tar.xz")):
            continue
        if not (R / f).is_file():
            continue
        files.append(f)
    # The staged, intentionally untracked container handoff is a build input
    # even if a developer has a broad local .git/info/exclude entry.
    if (R / 'packaging/mesa-handoff.sh').is_file():
        files.append('packaging/mesa-handoff.sh')
    files = sorted(set(files))
    prefix = f"NICKLABS-PS4-ORBIS-LINUX-{a.version}"
    buf = io.BytesIO()
    entries = []
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for f in files:
            p = R / f
            data = p.read_bytes()  # follows symlinks
            if len(data) > 32 * 1024 * 1024:
                raise SystemExit(f"Unexpected source input over 32MiB: {f}")
            entries.append({"path": f, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)})
            ti = tarfile.TarInfo(f"{prefix}/{f}")
            ti.size = len(data)
            ti.mtime = 0
            ti.mode = 0o755 if (p.stat().st_mode & 0o111) else 0o644
            ti.uid = ti.gid = 0
            ti.uname = ti.gname = "root"
            tar.addfile(ti, io.BytesIO(data))
        manifest = json.dumps({"schema": "orbis-core-build-source/1", "files": entries},
                              sort_keys=True, separators=(",", ":")).encode() + b"\n"
        ti = tarfile.TarInfo(f"{prefix}/BUILD-INPUT-MANIFEST.json")
        ti.size, ti.mtime, ti.mode, ti.uid, ti.gid = len(manifest), 0, 0o644, 0, 0
        tar.addfile(ti, io.BytesIO(manifest))
    Path(a.out).mkdir(parents=True, exist_ok=True)
    out = Path(a.out) / "NICKLABS-PS4-ORBIS-LINUX-SOURCE.tar.gz"
    with open(out, "wb") as fh:
        with gzip.GzipFile(fileobj=fh, mode="wb", mtime=0, compresslevel=9) as z:
            z.write(buf.getvalue())
    print(out)


if __name__ == "__main__":
    main()
