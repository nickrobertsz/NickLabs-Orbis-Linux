#!/usr/bin/env python3
"""Build the Orbis boot initramfs, reproducibly, without redistributing firmware.

  build_initramfs.py --src initramfs-ps4/ --firmware-from CURRENT_initramfs.cpio.gz \
                     --manifest manifests/firmware.json -o initramfs.cpio.gz

* --src: a checkout of GayCorp/initramfs-ps4 at the pinned commit (GPL-3.0;
  better-initramfs heritage, BSD-3). Same file selection as its bake.sh.
* --firmware-from: the initramfs.cpio.gz (or a directory) the operator already
  boots. Firmware groups listed in the manifest are copied from it and checked
  against the recorded SHA-256 values. Nothing is downloaded.
* Output is deterministic: sorted entries, uid/gid 0, mtime 0, gzip mtime 0.
"""
import argparse
import gzip
import hashlib
import io
import json
import os
import stat
import sys
from pathlib import Path

EXCLUDE = {"bake.sh", "initramfs.cpio.gz", "README.md", ".git", ".github", ".gitignore", ".gitattributes"}


def read_newc(data: bytes) -> dict[str, tuple[int, bytes]]:
    """Parse a newc cpio archive: {path: (mode, content)}."""
    out, off = {}, 0
    while off + 110 <= len(data):
        hdr = data[off:off + 110]
        if hdr[:6] not in (b"070701", b"070702"):
            raise ValueError(f"bad cpio magic at {off}")
        f = [int(hdr[6 + i * 8:14 + i * 8], 16) for i in range(13)]
        mode, filesize, namesize = f[1], f[6], f[11]
        name = data[off + 110: off + 110 + namesize - 1].decode()
        off = (off + 110 + namesize + 3) & ~3
        content = data[off: off + filesize]
        off = (off + filesize + 3) & ~3
        if name == "TRAILER!!!":
            break
        out[name.lstrip("./")] = (mode, content)
    return out


def write_newc(entries: list[tuple[str, int, bytes]]) -> bytes:
    buf = io.BytesIO()
    ino = 1

    def emit(name: str, mode: int, content: bytes, nlink=1):
        nonlocal ino
        nb = name.encode() + b"\0"
        fields = [ino, mode, 0, 0, nlink, 0, len(content), 0, 0, 0, 0, len(nb), 0]
        buf.write(b"070701" + b"".join(f"{x:08X}".encode() for x in fields))
        buf.write(nb)
        buf.write(b"\0" * ((-(110 + len(nb))) % 4))
        buf.write(content)
        buf.write(b"\0" * ((-len(content)) % 4))
        ino += 1
    for name, mode, content in entries:
        emit(name, mode, content, 2 if stat.S_ISDIR(mode) else 1)
    emit("TRAILER!!!", 0, b"")
    return buf.getvalue()


def collect_src(src: Path) -> dict[str, tuple[int, bytes]]:
    out = {}
    for root, dirs, files in os.walk(src):
        rel_root = Path(root).relative_to(src)
        dirs[:] = [d for d in dirs if not (rel_root == Path(".") and d in EXCLUDE)]
        for d in dirs:
            out[str(rel_root / d).lstrip("./")] = (stat.S_IFDIR | 0o755, b"")
        for f in files:
            if rel_root == Path(".") and f in EXCLUDE:
                continue
            p = Path(root) / f
            rel = str(rel_root / f).lstrip("./") if str(rel_root) != "." else f
            if p.is_symlink():
                out[rel] = (stat.S_IFLNK | 0o777, os.readlink(p).encode())
                continue
            executable = rel in ("init",) or rel.startswith(("bin/", "hooks/", "etc/profile.d/"))
            out[rel] = (stat.S_IFREG | (0o755 if executable else 0o644), p.read_bytes())
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--firmware-from", required=True, help="existing initramfs.cpio.gz or firmware directory")
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--groups", default="liverpool-gpu,edid", help="firmware groups to include")
    ap.add_argument("--git-hash", default=None, help="value for /GIT_HASH (default: read from --src)")
    ap.add_argument("-o", "--output", required=True)
    ap.add_argument("--allow-hash-mismatch", action="store_true")
    ap.add_argument("--allow-missing", action="store_true")
    a = ap.parse_args()
    src = Path(a.src)
    entries = collect_src(src)
    gh = a.git_hash
    if gh is None:
        try:
            import subprocess
            gh = subprocess.run(["git", "-C", str(src), "rev-parse", "--short=7", "HEAD"],
                                capture_output=True, text=True).stdout.strip() or "unknown"
        except OSError:
            gh = "unknown"
    entries["GIT_HASH"] = (stat.S_IFREG | 0o644, (gh + "\n").encode())
    entries["ORBIS_BUILD"] = (stat.S_IFREG | 0o644, b"built by NickLabs Orbis build_initramfs.py; firmware operator-supplied\n")

    fwsrc = Path(a.firmware_from)
    if fwsrc.is_dir():
        def get(rel):
            p = fwsrc / rel
            return p.read_bytes() if p.exists() else None
    else:
        arch = read_newc(gzip.decompress(fwsrc.read_bytes()))

        def get(rel):
            e = arch.get("lib/firmware/" + rel)
            return e[1] if e else None
    man = json.loads(Path(a.manifest).read_text())
    report, problems, missing = [], 0, 0
    for g in [x for x in a.groups.split(",") if x]:
        for f in man["groups"][g]["files"]:
            data = get(f["path"])
            if data is None:
                report.append(f"MISSING  {f['path']}")
                missing += 1
                continue
            h = hashlib.sha256(data).hexdigest()
            want = f.get("sha256")
            ok = want is None or h == want
            report.append(f"{'OK      ' if ok else 'MISMATCH'} {f['path']} {h[:16]}")
            if not ok:
                problems += 1
            parts = Path("lib/firmware") / f["path"]
            for parent in reversed(parts.parents):
                if str(parent) not in (".", ""):
                    entries.setdefault(str(parent), (stat.S_IFDIR | 0o755, b""))
            entries[str(parts)] = (stat.S_IFREG | 0o644, data)
    print("\n".join(report), file=sys.stderr)
    if missing and not a.allow_missing:
        print(f"{missing} firmware file(s) missing; refusing (--allow-missing builds a display-less initramfs)",
              file=sys.stderr)
        return 2
    if problems and not a.allow_hash_mismatch:
        print(f"{problems} firmware problem(s); refusing (use --allow-hash-mismatch to accept a different firmware revision)",
              file=sys.stderr)
        return 2
    ordered = [(n, m, c) for n, (m, c) in sorted(entries.items())]
    raw = write_newc(ordered)
    gz = io.BytesIO()
    with gzip.GzipFile(fileobj=gz, mode="wb", compresslevel=9, mtime=0) as z:
        z.write(raw)
    Path(a.output).write_bytes(gz.getvalue())
    print(f"wrote {a.output}: {len(ordered)} entries, sha256 {hashlib.sha256(gz.getvalue()).hexdigest()}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
