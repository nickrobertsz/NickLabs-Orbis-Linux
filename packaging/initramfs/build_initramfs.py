#!/usr/bin/env python3
"""Build the Orbis boot initramfs reproducibly, without redistributing firmware.

  build_initramfs.py --src initramfs-ps4/ --profile cuh-1215a \\
                     --firmware-from community-initramfs.cpio.gz -o out/initramfs.cpio.gz

* --src: a checkout of GayCorp/initramfs-ps4 at the commit pinned in
  manifests/sources.json (GPL-3.0; better-initramfs heritage, BSD-3). The same
  file selection as its bake.sh. The Orbis patches in packaging/initramfs/patches
  are applied to a private copy (never to --src). After patching, the
  root-selection self-check (packaging/initramfs/tests/root-selection.sh) runs
  and the build stops if it fails.
* --profile: a hardware profile from manifests/initramfs-profiles.json.
    required groups: every file must be present and match its recorded SHA-256.
                     Otherwise the build refuses. There is no override for a
                     missing required file.
    optional groups: included only when every file is present and verified.
                     An absent group is skipped and recorded. A partly present
                     or mismatching group is refused.
* --firmware-from: an initramfs.cpio.gz or a directory laid out like
  lib/firmware. Nothing is downloaded. Only the firmware is taken from it,
  never its init or scripts.
* Output is deterministic: sorted entries, uid/gid 0, mtime 0, gzip mtime 0.
  A build manifest (<output>.manifest.json) records the source commit, patches,
  profile, every firmware file with hash and provenance, and whether the
  result may be redistributed.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
EXCLUDE = {"bake.sh", "initramfs.cpio.gz", "README.md", ".git", ".github", ".gitignore", ".gitattributes", ".orbis-commit"}


class BuildError(Exception):
    pass


def read_newc(data: bytes) -> dict[str, tuple[int, bytes]]:
    """Parse a newc cpio archive: {path: (mode, content)}. Concatenated archives are merged, later wins."""
    out, off = {}, 0
    while off + 110 <= len(data):
        hdr = data[off:off + 110]
        if hdr[:6] not in (b"070701", b"070702"):
            if not hdr.strip(b"\0"):
                break  # padding after the trailer
            raise ValueError(f"bad cpio magic at {off}")
        f = [int(hdr[6 + i * 8:14 + i * 8], 16) for i in range(13)]
        mode, filesize, namesize = f[1], f[6], f[11]
        name = data[off + 110: off + 110 + namesize - 1].decode()
        off = (off + 110 + namesize + 3) & ~3
        content = data[off: off + filesize]
        off = (off + filesize + 3) & ~3
        if name == "TRAILER!!!":
            continue
        key = name[2:] if name.startswith("./") else name
        if key and key != ".":
            out[key] = (mode, content)
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
        dirs[:] = sorted(d for d in dirs if not (rel_root == Path(".") and d in EXCLUDE))
        for d in dirs:
            out[str(rel_root / d) if str(rel_root) != "." else d] = (stat.S_IFDIR | 0o755, b"")
        for f in files:
            if rel_root == Path(".") and f in EXCLUDE:
                continue
            p = Path(root) / f
            rel = str(rel_root / f) if str(rel_root) != "." else f
            if p.is_symlink():
                out[rel] = (stat.S_IFLNK | 0o777, os.readlink(p).encode())
                continue
            executable = rel == "init" or rel.startswith(("bin/", "hooks/", "etc/profile.d/"))
            out[rel] = (stat.S_IFREG | (0o755 if executable else 0o644), p.read_bytes())
    return out


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def src_commit(src: Path) -> str | None:
    """The source commit: from git, or from .orbis-commit (written by the orbis-platform package,
    which ships the pinned tree without .git)."""
    if (src / ".git").exists():
        try:
            r = subprocess.run(["git", "-C", str(src), "rev-parse", "HEAD"], capture_output=True, text=True)
            return r.stdout.strip() or None
        except OSError:
            return None
    marker = src / ".orbis-commit"
    return marker.read_text().strip() if marker.is_file() else None


def apply_patches(tree: Path, patches: list[Path]) -> None:
    for p in patches:
        r = subprocess.run(["git", "apply", "--whitespace=nowarn", str(p.resolve())], cwd=tree, capture_output=True, text=True)
        if r.returncode != 0:
            raise BuildError(f"patch {p.name} does not apply: {r.stderr.strip()}")


def self_check(tree: Path, script: Path) -> str:
    with tempfile.TemporaryDirectory(prefix="orbis-rootsel-") as work:
        r = subprocess.run(["sh", str(script.resolve()), str(tree), work], capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise BuildError("root-selection self-check failed:\n" + r.stdout + r.stderr)
    return r.stdout


class FirmwareSource:
    def __init__(self, path: Path | None):
        self.path = path
        self.kind = None
        self.sha256 = None
        self._arch: dict[str, tuple[int, bytes]] | None = None
        if path is None:
            return
        if path.is_dir():
            self.kind = "directory"
        else:
            raw = path.read_bytes()
            self.sha256 = sha256(raw)
            self.kind = "initramfs"
            self._arch = read_newc(gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw)

    def get(self, rel: str) -> bytes | None:
        if self.path is None:
            return None
        if self._arch is None:
            p = self.path / rel
            return p.read_bytes() if p.is_file() else None
        e = self._arch.get("lib/firmware/" + rel)
        return e[1] if e and stat.S_ISREG(e[0]) else None


def resolve_firmware(man: dict, profile: dict, fw: FirmwareSource, allow_mismatch: bool) -> tuple[list[dict], list[dict], list[str]]:
    """Return (files to include, skipped optional groups, refusal reasons)."""
    include, skipped, refusals = [], [], []
    groups = man["groups"]
    for kind in ("required_groups", "optional_groups"):
        required = kind == "required_groups"
        for g in profile.get(kind, []):
            if g not in groups:
                refusals.append(f"profile names unknown firmware group {g!r}")
                continue
            spec = groups[g]
            files = spec.get("files", [])
            if not files:
                (refusals if required else skipped).append(
                    f"group {g} has no recorded files" if required else {"group": g, "reason": "no files recorded"})
                continue
            found = []
            for f in files:
                data = fw.get(f["path"])
                found.append((f, data))
            present = [x for x in found if x[1] is not None]
            if not present:
                if required:
                    refusals += [f"MISSING required {g}: {f['path']}" for f, _ in found]
                else:
                    skipped.append({"group": g, "reason": "not present in firmware source"})
                continue
            if len(present) != len(found):
                miss = [f["path"] for f, d in found if d is None]
                refusals.append(f"{'required' if required else 'optional'} group {g} is incomplete, missing: {', '.join(miss)}")
                continue
            group_rows, bad = [], []
            for f, data in found:
                h = sha256(data)
                want = f.get("sha256")
                if want is None:
                    status = "unverified"
                elif h == want:
                    status = "verified"
                else:
                    status = "mismatch"
                if status != "verified":
                    bad.append(f"{status.upper()} {g}: {f['path']} sha256 {h}" + (f" (expected {want})" if want else ""))
                group_rows.append({"group": g, "path": f["path"], "bytes": len(data), "sha256": h,
                                   "status": status, "data": data,
                                   "redistributable": bool(spec.get("redistributable", False)),
                                   "licence": spec.get("licence") or spec.get("redistribution")})
            if bad and not allow_mismatch:
                refusals += bad
                continue
            for r in group_rows:
                if r["status"] != "verified":
                    r["status"] += "-accepted"
            include += group_rows
    return include, skipped, refusals


def build(src: Path, profile_name: str, out: Path, *, firmware_from: Path | None = None,
          manifest_path: Path = REPO / "manifests/firmware.json",
          profiles_path: Path = REPO / "manifests/initramfs-profiles.json",
          sources_path: Path | None = REPO / "manifests/sources.json",
          patches_dir: Path | None = REPO / "packaging/initramfs/patches",
          check_script: Path | None = REPO / "packaging/initramfs/tests/root-selection.sh",
          overlay_dir: Path | None = REPO / "packaging/initramfs/overlay",
          allow_mismatch: bool = False, allow_unpinned: bool = False, log=print) -> dict:
    man = json.loads(manifest_path.read_text())
    profiles = json.loads(profiles_path.read_text())["profiles"]
    if profile_name not in profiles:
        raise BuildError(f"unknown profile {profile_name!r}; known: {', '.join(sorted(profiles))}")
    profile = profiles[profile_name]

    commit = src_commit(src)
    pinned = None
    if sources_path is not None:
        pinned = json.loads(sources_path.read_text())["initramfs"]["commit"]
        if commit != pinned and not allow_unpinned:
            raise BuildError(f"--src is at {commit or 'an unknown commit'}, pinned is {pinned} "
                             "(check it out, or pass --allow-unpinned-src)")

    patches = sorted(patches_dir.glob("*.patch")) if patches_dir and patches_dir.is_dir() else []
    fw = FirmwareSource(firmware_from)
    if fw.path is None and profile.get("required_groups"):
        raise BuildError(f"profile {profile_name} requires firmware ({', '.join(profile['required_groups'])}); "
                         "give --firmware-from")
    include, skipped, refusals = resolve_firmware(man, profile, fw, allow_mismatch)
    for r in include:
        log(f"{r['status']:<18} {r['group']:<16} {r['path']} {r['sha256'][:16]}")
    for s in skipped:
        log(f"{'skipped (optional)':<18} {s['group']:<16} {s['reason']}")
    if refusals:
        raise BuildError("firmware check failed; nothing written:\n  " + "\n  ".join(refusals) +
                         ("\n(a different firmware revision can be accepted with --allow-hash-mismatch; "
                          "missing required files cannot)" if any("MISMATCH" in x or "UNVERIFIED" in x for x in refusals) else ""))

    with tempfile.TemporaryDirectory(prefix="orbis-initramfs-") as tmp:
        tree = Path(tmp) / "src"
        shutil.copytree(src, tree, symlinks=True, ignore=shutil.ignore_patterns(".git"))
        apply_patches(tree, patches)
        check_out = self_check(tree, check_script) if (patches and check_script and check_script.exists()) else None
        entries = collect_src(tree)
    overlay = collect_src(overlay_dir) if overlay_dir and overlay_dir.is_dir() else {}
    clash = sorted(k for k, (m, _) in overlay.items() if k in entries and not (stat.S_ISDIR(m) and stat.S_ISDIR(entries[k][0])))
    if clash:
        raise BuildError("overlay would replace upstream files (change them with a patch instead): " + ", ".join(clash))
    entries.update(overlay)

    short = (commit or "unknown")[:7]
    entries["GIT_HASH"] = (stat.S_IFREG | 0o644, (short + (f"+orbis{len(patches)}" if patches else "") + "\n").encode())
    for r in include:
        parts = Path("lib/firmware") / r["path"]
        for parent in reversed(parts.parents):
            if str(parent) not in (".", ""):
                entries.setdefault(str(parent), (stat.S_IFDIR | 0o755, b""))
        entries[str(parts)] = (stat.S_IFREG | 0o644, r["data"])
    redistributable = all(r["redistributable"] for r in include)
    entries["ORBIS_BUILD"] = (stat.S_IFREG | 0o644, json.dumps(
        {"builder": "NickLabs Orbis build_initramfs.py", "profile": profile_name,
         "groups": sorted({r["group"] for r in include}), "redistributable": redistributable},
        sort_keys=True).encode() + b"\n")

    ordered = [(n, m, c) for n, (m, c) in sorted(entries.items())]
    raw = write_newc(ordered)
    gz = io.BytesIO()
    with gzip.GzipFile(fileobj=gz, mode="wb", compresslevel=9, mtime=0, filename="") as z:
        z.write(raw)
    data = gz.getvalue()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    report = {
        "schema": 1,
        "output": {"file": out.name, "sha256": sha256(data), "bytes": len(data), "entries": len(ordered)},
        "profile": dict(profile, name=profile_name),
        "source": {"repo": "https://github.com/GayCorp/initramfs-ps4", "commit": commit, "pinned": pinned,
                   "pin_ok": commit == pinned if pinned else None},
        "patches": [{"file": p.name, "sha256": sha256(p.read_bytes())} for p in patches],
        "overlay": [{"path": k, "sha256": sha256(c)} for k, (m, c) in sorted(overlay.items()) if stat.S_ISREG(m)],
        "self_check": check_out.strip().splitlines() if check_out else None,
        "firmware_source": None if fw.path is None else {"kind": fw.kind, "name": fw.path.name, "sha256": fw.sha256},
        "firmware": [{k: v for k, v in r.items() if k != "data"} for r in include],
        "skipped_optional_groups": skipped,
        "redistributable": redistributable,
        "redistribution_note": ("contains no firmware" if not include else
                                "contains firmware without established redistribution rights: keep this initramfs private "
                                "and never place it in a release archive") if not redistributable else
                               "all included firmware is marked redistributable in manifests/firmware.json",
        "loader_note": man["groups"].get("liverpool-gpu", {}).get("loader_note"),
    }
    Path(str(out) + ".manifest.json").write_text(json.dumps(report, indent=1) + "\n")
    log(f"wrote {out}: {len(ordered)} entries, sha256 {report['output']['sha256']}"
        f"{'' if redistributable else '  [PRIVATE: contains firmware, do not redistribute]'}")
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--src", required=True, help="GayCorp/initramfs-ps4 checkout at the pinned commit")
    ap.add_argument("--profile", required=True, help="profile from manifests/initramfs-profiles.json (e.g. cuh-1215a)")
    ap.add_argument("--firmware-from", help="initramfs.cpio.gz or firmware directory to take firmware from")
    ap.add_argument("--manifest", default=str(REPO / "manifests/firmware.json"))
    ap.add_argument("--profiles", default=str(REPO / "manifests/initramfs-profiles.json"))
    ap.add_argument("--sources", default=str(REPO / "manifests/sources.json"))
    ap.add_argument("--patches", default=str(REPO / "packaging/initramfs/patches"))
    ap.add_argument("--check-script", default=str(REPO / "packaging/initramfs/tests/root-selection.sh"))
    ap.add_argument("--overlay", default=str(REPO / "packaging/initramfs/overlay"),
                    help="Orbis-owned files added to the initramfs (may not replace upstream files)")
    ap.add_argument("-o", "--output", required=True)
    ap.add_argument("--allow-hash-mismatch", action="store_true",
                    help="accept present-but-different firmware (another revision); recorded in the manifest")
    ap.add_argument("--allow-unpinned-src", action="store_true")
    a = ap.parse_args(argv)
    try:
        build(Path(a.src), a.profile, Path(a.output),
              firmware_from=Path(a.firmware_from) if a.firmware_from else None,
              manifest_path=Path(a.manifest), profiles_path=Path(a.profiles),
              sources_path=Path(a.sources), patches_dir=Path(a.patches), check_script=Path(a.check_script),
              overlay_dir=Path(a.overlay),
              allow_mismatch=a.allow_hash_mismatch, allow_unpinned=a.allow_unpinned_src,
              log=lambda s: print(s, file=sys.stderr))
    except BuildError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
