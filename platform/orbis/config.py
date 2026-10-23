"""Configuration loading.

Layering (later wins):
  1. /usr/share/orbis/orbis.toml        packaged defaults
  2. /etc/orbis/orbis.toml               administrator overrides
  3. /var/lib/orbis/settings.json        values changed through the API

Profiles live in /usr/share/orbis/profiles/<mode>.toml and may be
overridden file-by-file from /etc/orbis/profiles/<mode>.toml.
"""

from __future__ import annotations

import copy
import json
import os
import tempfile
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import MODES, paths


class ConfigError(Exception):
    pass


def deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _load_toml(path: Path) -> dict:
    try:
        with open(path, "rb") as fh:
            return tomllib.load(fh)
    except FileNotFoundError:
        return {}
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: {exc}") from exc


def _load_json(path: Path) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: expected a JSON object")
    return data


def atomic_write(path: Path, data: bytes, mode: int = 0o644) -> None:
    """Write-then-rename with fsync of file and directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
    try:
        dfd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except OSError:
        pass  # Some filesystems (vfat via some drivers) refuse dir fsync.


def load_config() -> dict:
    cfg = _load_toml(paths.share_dir() / "orbis.toml")
    cfg = deep_merge(cfg, _load_toml(paths.etc_dir() / "orbis.toml"))
    cfg = deep_merge(cfg, _load_json(paths.state_dir() / "settings.json"))
    return cfg


#: Settings that may be changed through the API (dotted keys -> validator).
_SETTABLE: dict[str, Any] = {
    "orbis.switch_policy": lambda v: v in ("auto", "fast", "reboot"),
    "ai.backend": lambda v: v in ("cpu", "vulkan", "auto"),
    "ai.model": lambda v: isinstance(v, str) and "/" not in v and len(v) < 200,
    "ai.ctx_size": lambda v: isinstance(v, int) and 256 <= v <= 32768,
    "ai.power_preset": lambda v: v in ("quiet", "balanced", "performance"),
    "ai.lan_api": lambda v: isinstance(v, bool),
    "gaming.frontend": lambda v: v in ("steam-gamepadui", "es-de", "kodi", "none"),
}


def set_setting(key: str, value: Any) -> dict:
    check = _SETTABLE.get(key)
    if check is None:
        raise ConfigError(f"setting {key!r} is not changeable through the API")
    if not check(value):
        raise ConfigError(f"invalid value for {key}: {value!r}")
    path = paths.state_dir() / "settings.json"
    data = _load_json(path)
    section, _, leaf = key.partition(".")
    data.setdefault(section, {})[leaf] = value
    atomic_write(path, json.dumps(data, indent=2, sort_keys=True).encode())
    return data


@dataclass
class Profile:
    name: str
    title: str
    description: str
    target: str
    display: bool
    vram_min_mb: int
    vram_preferred_mb: int
    start_units: list[str] = field(default_factory=list)
    owned_patterns: list[str] = field(default_factory=list)
    allow_gpu_clients: bool = True
    readiness: list[dict] = field(default_factory=list)
    power_preset: str = "balanced"
    sysctl: dict[str, str] = field(default_factory=dict)
    budget: dict[str, int] = field(default_factory=dict)
    backend_overrides: dict[str, dict] = field(default_factory=dict)
    raw: dict = field(default_factory=dict)

    def vram_for_backend(self, backend: str | None) -> tuple[int, int]:
        ov = self.backend_overrides.get(backend or "", {})
        return (int(ov.get("min_mb", self.vram_min_mb)),
                int(ov.get("preferred_mb", self.vram_preferred_mb)))

    def to_dict(self) -> dict:
        return {
            "name": self.name, "title": self.title,
            "description": self.description, "target": self.target,
            "display": self.display,
            "vram": {"min_mb": self.vram_min_mb,
                     "preferred_mb": self.vram_preferred_mb,
                     "backend_overrides": self.backend_overrides},
            "start_units": self.start_units,
            "owned_patterns": self.owned_patterns,
            "allow_gpu_clients": self.allow_gpu_clients,
            "power_preset": self.power_preset,
            "budget": self.budget,
        }


def _profile_from_dict(mode: str, d: dict, source: Path) -> Profile:
    try:
        prof = d["profile"]
        vram = d.get("vram", {})
        services = d.get("services", {})
        return Profile(
            name=prof["name"],
            title=prof.get("title", mode.upper()),
            description=prof.get("description", ""),
            target=prof["target"],
            display=bool(prof.get("display", True)),
            vram_min_mb=int(vram.get("min_mb", 32)),
            vram_preferred_mb=int(vram.get("preferred_mb", 1024)),
            start_units=list(services.get("start", [])),
            owned_patterns=list(services.get("owned", [])),
            allow_gpu_clients=bool(services.get("allow_gpu_clients", True)),
            readiness=list(d.get("readiness", {}).get("checks", [])),
            power_preset=d.get("power", {}).get("preset", "balanced"),
            sysctl={k: str(v) for k, v in d.get("sysctl", {}).items()},
            budget={k: int(v) for k, v in d.get("budget", {}).items()},
            backend_overrides=dict(vram.get("backend", {})),
            raw=d,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ConfigError(f"{source}: invalid profile: {exc}") from exc


def load_profile(mode: str) -> Profile:
    if mode not in MODES:
        raise ConfigError(f"unknown mode {mode!r}; expected one of {', '.join(MODES)}")
    for base in (paths.etc_dir() / "profiles", paths.share_dir() / "profiles"):
        f = base / f"{mode}.toml"
        if f.exists():
            prof = _profile_from_dict(mode, _load_toml(f), f)
            if prof.name != mode:
                raise ConfigError(f"{f}: profile.name {prof.name!r} != {mode!r}")
            return prof
    raise ConfigError(f"no profile file for mode {mode!r}")


def load_profiles() -> dict[str, Profile]:
    return {m: load_profile(m) for m in MODES}
