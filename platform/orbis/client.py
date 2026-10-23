"""Minimal client for the orbisd Unix-socket API."""

from __future__ import annotations

import json
import socket
import time
from pathlib import Path

from . import paths


class DaemonUnavailable(Exception):
    pass


class ApiError(Exception):
    pass


class Client:
    def __init__(self, sock_path: str | Path | None = None, timeout: float = 30):
        self.path = str(sock_path or paths.daemon_socket())
        self.timeout = timeout
        self._id = 0

    def call(self, method: str, **params):
        self._id += 1
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(self.timeout)
            s.connect(self.path)
        except OSError as exc:
            raise DaemonUnavailable(f"orbisd not reachable at {self.path}: {exc}") from exc
        try:
            s.sendall((json.dumps({"id": self._id, "method": method, "params": params}) + "\n").encode())
            buf = b""
            while not buf.endswith(b"\n"):
                chunk = s.recv(1 << 16)
                if not chunk:
                    break
                buf += chunk
        finally:
            s.close()
        try:
            resp = json.loads(buf)
        except json.JSONDecodeError as exc:
            raise ApiError("malformed response from orbisd") from exc
        if not resp.get("ok"):
            raise ApiError(resp.get("error", "unknown error"))
        return resp.get("result")

    def wait_job(self, jid: str, poll: float = 0.5, timeout: float = 900, on_progress=None):
        seen = 0
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            j = self.call("job", id=jid)
            prog = j.get("progress", [])
            if on_progress:
                for p in prog[seen:]:
                    on_progress(p["msg"])
            seen = len(prog)
            if j["state"] != "running":
                return j
            time.sleep(poll)
        raise ApiError(f"job {jid} did not finish in {timeout:.0f}s")
