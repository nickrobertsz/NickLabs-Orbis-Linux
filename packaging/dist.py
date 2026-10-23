#!/usr/bin/env python3
"""Deterministic source tarball of the git-tracked tree (symlinks dereferenced)."""
import argparse
import gzip
import io
import subprocess
import tarfile
from pathlib import Path

R = Path(__file__).resolve().parents[1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", required=True)
    ap.add_argument("--out", default=str(R / "out"))
    a = ap.parse_args()
    files = subprocess.run(["git", "-C", str(R), "ls-files", "-z"], capture_output=True, check=True).stdout.split(b"\0")
    files = sorted(f.decode() for f in files if f)
    prefix = f"NICKLABS-PS4-ORBIS-LINUX-{a.version}"
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for f in files:
            p = R / f
            data = p.read_bytes()  # follows symlinks
            ti = tarfile.TarInfo(f"{prefix}/{f}")
            ti.size = len(data)
            ti.mtime = 0
            ti.mode = 0o755 if (p.stat().st_mode & 0o111) else 0o644
            ti.uid = ti.gid = 0
            ti.uname = ti.gname = "root"
            tar.addfile(ti, io.BytesIO(data))
    Path(a.out).mkdir(parents=True, exist_ok=True)
    out = Path(a.out) / "NICKLABS-PS4-ORBIS-LINUX-SOURCE.tar.gz"
    with open(out, "wb") as fh:
        with gzip.GzipFile(fileobj=fh, mode="wb", mtime=0, compresslevel=9) as z:
            z.write(buf.getvalue())
    print(out)


if __name__ == "__main__":
    main()
