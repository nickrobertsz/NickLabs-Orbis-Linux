"""Instruction-set safety audit for AMD Jaguar (PS4 / PS4 Pro CPU).

Jaguar (btver2) has SSE4.2, AVX (128/256-bit float), F16C, BMI1, MOVBE, AES,
PCLMUL — and lacks AVX2, FMA3, FMA4, BMI2 and AVX-512. It satisfies
x86-64-v2 but NOT x86-64-v3. A binary compiled for v3 (CachyOS v3 repos,
RHEL 10, `-march=native` on a Ryzen) dies with SIGILL here.

Three layers of evidence:
  1. package metadata  pacman `Architecture` / repo names / per-package %ARCH%
  2. ELF notes         GNU_PROPERTY_X86_ISA_1_NEEDED, when the toolchain emits it
  3. disassembly       how widely v3-only instructions are spread (objdump)

Layer 3 distinguishes *dispatched* code (a few functions with AVX2, guarded
by CPUID at run time; e.g. Mesa, glibc) from code *compiled for* v3 (BMI2 and
256-bit integer ops sprinkled across hundreds of functions).
"""

from __future__ import annotations

import os
import re
import shutil
import struct
import subprocess
from collections import Counter, defaultdict
from pathlib import Path

from . import paths

GNU_PROPERTY_X86_ISA_1_NEEDED = 0xC0008002
GNU_PROPERTY_X86_ISA_1_USED = 0xC0010002
ISA_BITS = {1: "x86-64-baseline", 2: "x86-64-v2", 4: "x86-64-v3", 8: "x86-64-v4"}

BMI2 = {"shlx", "shrx", "sarx", "rorx", "mulx", "pdep", "pext", "bzhi"}
AVX2_NAMED = {"vpbroadcastb", "vpbroadcastw", "vpbroadcastd", "vpbroadcastq", "vbroadcasti128",
              "vperm2i128", "vinserti128", "vextracti128", "vpermd", "vpermq", "vpermps",
              "vpermpd", "vpmaskmovd", "vpmaskmovq", "vpsllvd", "vpsllvq", "vpsrlvd", "vpsrlvq",
              "vpsravd", "vpblendd"}
FMA_RE = re.compile(r"^vf(n?)m(add|sub|addsub|subadd)")
GATHER_RE = re.compile(r"^v(p)?gather")
YMM_INT_RE = re.compile(r"^vp\w+$")
BAD_REPO_RE = re.compile(r"(x86[-_]64[-_]v[34]|znver\d|[-_]v[34]$)", re.I)


# ------------------------------------------------------------------ ELF notes
def elf_isa_needed(path: str | Path) -> dict:
    """Parse PT_NOTE segments for x86 ISA properties. Returns {} if absent."""
    out: dict = {}
    try:
        with open(path, "rb") as fh:
            ident = fh.read(16)
            if ident[:4] != b"\x7fELF" or ident[4] != 2 or ident[5] != 1:
                return {"error": "not an ELF64 little-endian file"}
            hdr = fh.read(48)
            (e_type, e_machine, _v, _entry, e_phoff, _shoff, _flags, _ehsize,
             e_phentsize, e_phnum, *_rest) = struct.unpack("<HHIQQQIHHHHHH", hdr)
            if e_machine != 62:
                return {"error": "not x86-64"}
            for i in range(e_phnum):
                fh.seek(e_phoff + i * e_phentsize)
                p_type, _p_flags, p_offset, _va, _pa, p_filesz, _memsz, _align = struct.unpack(
                    "<IIQQQQQQ", fh.read(56))
                if p_type != 4:  # PT_NOTE
                    continue
                fh.seek(p_offset)
                data = fh.read(p_filesz)
                off = 0
                while off + 12 <= len(data):
                    namesz, descsz, ntype = struct.unpack_from("<III", data, off)
                    name = data[off + 12: off + 12 + namesz]
                    doff = off + 12 + ((namesz + 7) & ~7 if p_filesz % 8 == 0 else (namesz + 3) & ~3)
                    desc = data[doff: doff + descsz]
                    if ntype == 5 and name.rstrip(b"\x00") == b"GNU":  # NT_GNU_PROPERTY_TYPE_0
                        poff = 0
                        while poff + 8 <= len(desc):
                            pr_type, pr_datasz = struct.unpack_from("<II", desc, poff)
                            val = desc[poff + 8: poff + 8 + pr_datasz]
                            if pr_type in (GNU_PROPERTY_X86_ISA_1_NEEDED, GNU_PROPERTY_X86_ISA_1_USED) and len(val) >= 4:
                                bits = struct.unpack_from("<I", val)[0]
                                key = "needed" if pr_type == GNU_PROPERTY_X86_ISA_1_NEEDED else "used"
                                out[key] = [n for b, n in ISA_BITS.items() if bits & b]
                            poff += 8 + ((pr_datasz + 7) & ~7)
                    off = doff + ((descsz + 7) & ~7 if p_filesz % 8 == 0 else (descsz + 3) & ~3)
    except (OSError, struct.error) as exc:
        return {"error": str(exc)}
    return out


# ------------------------------------------------------------------ disassembly
def classify_insn(mnem: str, operands: str) -> str | None:
    m = mnem.split(".")[0]
    if m in BMI2:
        return "bmi2"
    if FMA_RE.match(m):
        return "fma"
    if GATHER_RE.match(m):
        return "avx2"
    if m in AVX2_NAMED:
        return "avx2"
    if "%zmm" in operands or re.search(r"%k[0-7]\b", operands) or "{%k" in operands:
        return "avx512"
    if YMM_INT_RE.match(m) and "%ymm" in operands and m not in ("vptest",):
        return "avx2"  # 256-bit integer SIMD is AVX2-only
    return None


def disasm_profile(path: str | Path, objdump: str | None = None, max_seconds: int = 600) -> dict:
    objdump = objdump or shutil.which("objdump")
    if not objdump:
        return {"error": "objdump not available (install binutils)"}
    try:
        r = subprocess.run([objdump, "-d", "--no-show-raw-insn", "-M", "att", str(path)],
                           capture_output=True, text=True, timeout=max_seconds)
    except subprocess.TimeoutExpired:
        return {"error": "objdump timed out"}
    func = None
    funcs_total = 0
    insns_total = 0
    per_cat = Counter()
    funcs_with = defaultdict(set)
    examples: dict[str, str] = {}
    fre = re.compile(r"^[0-9a-f]+ <(.+)>:$")
    ire = re.compile(r"^\s*[0-9a-f]+:\s+([a-z][\w.]*)\s*(.*)$")
    for line in r.stdout.splitlines():
        m = fre.match(line)
        if m:
            func = m.group(1)
            funcs_total += 1
            continue
        m = ire.match(line)
        if not m:
            continue
        insns_total += 1
        cat = classify_insn(m.group(1), m.group(2))
        if cat:
            per_cat[cat] += 1
            funcs_with[cat].add(func)
            examples.setdefault(cat, f"{m.group(1)} {m.group(2)[:60]} in <{func}>")
    spread = {k: len(v) for k, v in funcs_with.items()}
    any_funcs = set().union(*funcs_with.values()) if funcs_with else set()
    n_any = len(any_funcs)
    threshold = max(8, funcs_total // 100)
    stripped = funcs_total < 64 and insns_total > 20000
    bmi2_density = per_cat.get("bmi2", 0) / insns_total if insns_total else 0.0
    if not per_cat:
        verdict = "baseline-safe"
    elif bmi2_density > 5e-4 or ("bmi2" in spread and spread["bmi2"] > threshold and not stripped):
        # Compilers emit SHLX/SARX/SHRX for ordinary shifts under -march=x86-64-v3;
        # hand-dispatched SIMD kernels almost never contain them.
        verdict = "compiled-for-v3"
    elif stripped:
        # No symbols: spread cannot be measured. AVX2 without BMI2 density is the
        # signature of CPUID-dispatched kernels (e.g. BLAKE3/memcpy variants).
        verdict = "dispatched"
    elif n_any > threshold:
        verdict = "suspect-v3"
    else:
        verdict = "dispatched"          # few functions: runtime CPUID dispatch is plausible
    return {"functions": funcs_total, "stripped": stripped, "instructions_total": insns_total,
            "instructions": dict(per_cat), "bmi2_density": round(bmi2_density, 6),
            "functions_with": spread, "functions_with_any": n_any, "threshold": threshold,
            "verdict": verdict, "examples": examples}


def audit_file(path: str | Path, deep: bool = True) -> dict:
    res = {"path": str(path), "elf_note": elf_isa_needed(path)}
    needed = res["elf_note"].get("needed") or []
    if any(n in ("x86-64-v3", "x86-64-v4") for n in needed):
        res["verdict"] = "unsafe"
        res["reason"] = f"ELF note requires {needed}"
        return res
    if deep:
        res["disasm"] = disasm_profile(path)
        v = res["disasm"].get("verdict")
        res["verdict"] = {"baseline-safe": "safe", "dispatched": "safe-if-dispatched",
                          "suspect-v3": "suspect", "compiled-for-v3": "unsafe"}.get(v, "unknown")
    else:
        res["verdict"] = "unknown"
    return res


# ------------------------------------------------------------------ pacman
def pacman_audit() -> dict:
    conf = paths.p("etc/pacman.conf")
    out = {"architecture": None, "bad_repos": [], "foreign_arch_packages": []}
    try:
        text = conf.read_text()
    except OSError:
        out["error"] = "no /etc/pacman.conf"
        return out
    for line in text.splitlines():
        s = line.strip()
        m = re.match(r"^Architecture\s*=\s*(.+)$", s)
        if m:
            out["architecture"] = m.group(1).split()
        m = re.match(r"^\[(.+)\]$", s)
        if m and m.group(1) != "options" and BAD_REPO_RE.search(m.group(1)):
            out["bad_repos"].append(m.group(1))
    local = paths.p("var/lib/pacman/local")
    if local.is_dir():
        for d in local.iterdir():
            desc = d / "desc"
            try:
                t = desc.read_text()
            except OSError:
                continue
            m = re.search(r"%ARCH%\n(\S+)", t)
            if m and m.group(1) not in ("x86_64", "any"):
                out["foreign_arch_packages"].append({"package": d.name, "arch": m.group(1)})
    arch = out["architecture"] or []
    out["ok"] = (not out["bad_repos"] and not out["foreign_arch_packages"]
                 and not any(a not in ("x86_64", "auto") for a in arch))
    return out


def iter_elf(root: Path, limit: int = 100000):
    n = 0
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            p = Path(dirpath) / f
            try:
                if p.is_symlink() or not p.is_file():
                    continue
                with open(p, "rb") as fh:
                    if fh.read(4) != b"\x7fELF":
                        continue
            except OSError:
                continue
            yield p
            n += 1
            if n >= limit:
                return


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json
    ap = argparse.ArgumentParser(description="Audit binaries/packages for Jaguar (x86-64-v2) safety")
    ap.add_argument("paths", nargs="*", help="ELF files or directories")
    ap.add_argument("--pacman", action="store_true", help="audit pacman architecture/repos")
    ap.add_argument("--no-disasm", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    report = {}
    rc = 0
    if a.pacman:
        report["pacman"] = pacman_audit()
        if not report["pacman"].get("ok", False):
            rc = 2
    files = []
    for p in a.paths:
        pp = Path(p)
        files += list(iter_elf(pp)) if pp.is_dir() else [pp]
    report["files"] = [audit_file(f, deep=not a.no_disasm) for f in files]
    if any(f.get("verdict") == "unsafe" for f in report["files"]):
        rc = 2
    elif any(f.get("verdict") == "suspect" for f in report["files"]) and rc == 0:
        rc = 1
    if a.json:
        print(json.dumps(report, indent=2))
    else:
        if "pacman" in report:
            pa = report["pacman"]
            print(f"pacman: architecture={pa.get('architecture')} ok={pa.get('ok')} "
                  f"bad_repos={pa.get('bad_repos')} foreign={len(pa.get('foreign_arch_packages', []))}")
        for f in report["files"]:
            d = f.get("disasm") or {}
            print(f"{f['verdict']:20s} {f['path']}  {d.get('instructions', {})} "
                  f"funcs_with={d.get('functions_with', {})}/{d.get('functions', '?')} note={f['elf_note'] or '-'}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
