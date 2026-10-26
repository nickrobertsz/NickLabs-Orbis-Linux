"""LAN-only network policy status (orbis-lan-only.service / nftables table inet orbis_lan_only)."""

from __future__ import annotations

import shutil
import subprocess

from . import paths

TABLE = ("inet", "orbis_lan_only")
ALLOW_FILE = "etc/orbis/allow-internet"


def expected(cfg: dict) -> bool:
    """Whether the LAN-only policy should be active, from [network] lan_only and the opt-out file."""
    want = bool(cfg.get("network", {}).get("lan_only", True))
    return want and not paths.p(ALLOW_FILE).exists()


def active(run=subprocess.run) -> tuple[bool | None, str]:
    """(True/False, detail) when nft could answer; (None, reason) when it could not be checked."""
    nft = shutil.which("nft")
    if not nft:
        return None, "nft not installed"
    try:
        r = run([nft, "list", "table", *TABLE], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"nft failed: {exc}"
    if r.returncode == 0:
        ok = "chain output" in r.stdout and "@lan4" in r.stdout
        return ok, "table loaded" if ok else "table present but incomplete"
    err = (r.stderr or "").strip()
    if "Operation not permitted" in err or "Permission denied" in err:
        return None, "needs root to read nftables (run: sudo orbis doctor)"
    return False, err or "table not loaded"


def status(cfg: dict, run=subprocess.run) -> dict:
    exp = expected(cfg)
    act, detail = active(run)
    if act is None:
        sev = "INFO"
    elif exp and not act:
        sev = "FAIL"
    elif not exp and act:
        sev = "INFO"
    else:
        sev = "OK"
    if exp:
        msg = {True: "LAN-only policy active: public internet blocked",
               False: f"LAN-only policy expected but NOT active ({detail})",
               None: f"LAN-only policy expected; could not verify ({detail})"}[act]
    else:
        msg = {True: "LAN-only policy active although disabled in config/allow-internet",
               False: "LAN-only policy disabled: the console can reach the public internet",
               None: f"LAN-only policy disabled; state unknown ({detail})"}[act]
    return {"severity": sev, "message": msg, "expected": exp, "active": act, "detail": detail}
