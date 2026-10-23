"""Filesystem locations used by Orbis.

Every path is resolved relative to ``ORBIS_ROOT`` (default ``/``) so the
whole platform layer can be exercised against a fixture tree in tests and
against a mounted, not-yet-booted root filesystem during image builds.
"""

from __future__ import annotations

import os
from pathlib import Path


def root() -> Path:
    return Path(os.environ.get("ORBIS_ROOT", "/"))


def p(*parts: str) -> Path:
    """Return a path below ORBIS_ROOT. Absolute parts are re-rooted."""
    base = root()
    out = base
    for part in parts:
        out = out / part.lstrip("/")
    return out


# Static data shipped by the package (profiles, capability matrix, UI).
def share_dir() -> Path:
    override = os.environ.get("ORBIS_SHARE_DIR")
    return Path(override) if override else p("usr/share/orbis")


def etc_dir() -> Path:
    return p("etc/orbis")


def state_dir() -> Path:
    return p("var/lib/orbis")


def log_dir() -> Path:
    return p("var/log/orbis")


def run_dir() -> Path:
    return p("run/orbis")


def daemon_socket() -> Path:
    override = os.environ.get("ORBIS_SOCKET")
    return Path(override) if override else run_dir() / "orbisd.sock"


def sysfs(*parts: str) -> Path:
    return p("sys", *parts)


def procfs(*parts: str) -> Path:
    return p("proc", *parts)


def desired_mode_file() -> Path:
    # One word, read by the early-boot generator (POSIX sh). Keep it simple.
    return state_dir() / "desired-mode"


def transitions_log() -> Path:
    return log_dir() / "transitions.jsonl"
