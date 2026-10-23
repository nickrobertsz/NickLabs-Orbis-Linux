"""orbis-web — the unprivileged LAN dashboard and API gateway.

Runs as user `orbis-web` (member of `orbis` and `orbis-admin` so it can ask
orbisd for changes on behalf of an authenticated operator). It holds no
privileges itself: every action is an orbisd API call.

Security model
  * Clients must come from loopback/private/link-local addresses (configurable).
  * Login with the dashboard token (/etc/orbis/web-token, generated at install);
    constant-time comparison, per-IP rate limit.
  * Session cookie: HttpOnly, SameSite=Strict. State-changing requests also need
    the per-session CSRF token in the X-Orbis-CSRF header.
  * Strict CSP; the UI loads nothing from the internet.
  * The OpenAI-compatible endpoint (/v1/*) is exposed only when ai.lan_api is
    enabled, and requires `Authorization: Bearer <key>` (/etc/orbis/ai-api-key).
    llama-server itself listens on 127.0.0.1 only.
  * Socket activation: when started by orbis-web.socket the process exits after
    `web.idle_exit_s` without requests, so the dashboard costs no RAM while
    nobody looks at it (useful in AI mode).
"""

from __future__ import annotations

import hmac
import ipaddress
import json
import mimetypes
import os
import secrets
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import API_VERSION, __version__, config, paths
from .client import ApiError, Client, DaemonUnavailable

SESSION_TTL = 12 * 3600
CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
       "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")

GET_ROUTES = {
    "/api/v1/hello": ("hello", False), "/api/v1/status": ("status", False),
    "/api/v1/capabilities": ("capabilities", False), "/api/v1/budget": ("budget", False),
    "/api/v1/ai/status": ("ai.status", False), "/api/v1/ai/models": ("ai.models", False),
    "/api/v1/logs": ("logs", False), "/api/v1/gaming/status": ("gaming.status", False),
    "/api/v1/doctor/last": ("doctor.last", False),
}
POST_ROUTES = {
    "/api/v1/plan": "plan", "/api/v1/switch": "switch", "/api/v1/doctor/run": "doctor.run",
    "/api/v1/ai/set": "ai.set", "/api/v1/settings/set": "settings.set", "/api/v1/reboot": "reboot",
}


class WebState:
    def __init__(self, cfg: dict, client: Client, token: str | None, api_key: str | None):
        self.cfg = cfg
        self.client = client
        self.token = token
        self.api_key = api_key
        self.sessions: dict[str, dict] = {}
        self.attempts: dict[str, list[float]] = {}
        self.last_request = time.monotonic()
        nets = cfg.get("web", {}).get("allow_networks") or [
            "127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16",
            "::1/128", "fc00::/7", "fe80::/10"]
        self.allow = [ipaddress.ip_network(n) for n in nets]
        self.ui_dir = paths.share_dir() / "ui"
        if not self.ui_dir.is_dir():
            self.ui_dir = Path(__file__).resolve().parents[2] / "ui"

    def allowed_ip(self, ip: str) -> bool:
        try:
            a = ipaddress.ip_address(ip.split("%")[0])
        except ValueError:
            return False
        if isinstance(a, ipaddress.IPv6Address) and a.ipv4_mapped:
            a = a.ipv4_mapped
        return any(a in n for n in self.allow if n.version == a.version)

    def rate_ok(self, ip: str) -> bool:
        now = time.monotonic()
        lst = [t for t in self.attempts.get(ip, []) if now - t < 60]
        lst.append(now)
        self.attempts[ip] = lst
        return len(lst) <= 10

    def new_session(self) -> dict:
        s = {"id": secrets.token_hex(32), "csrf": secrets.token_hex(16), "exp": time.time() + SESSION_TTL}
        self.sessions[s["id"]] = s
        return s

    def session(self, cookie_header: str | None) -> dict | None:
        if not cookie_header:
            return None
        for part in cookie_header.split(";"):
            k, _, v = part.strip().partition("=")
            if k == "orbis_session":
                s = self.sessions.get(v)
                if s and s["exp"] > time.time():
                    return s
        return None


def _read_secret(path: Path) -> str | None:
    try:
        v = path.read_text().strip()
        return v or None
    except OSError:
        return None


class Handler(BaseHTTPRequestHandler):
    server_version = f"orbis-web/{__version__}"
    st: WebState  # injected

    def log_message(self, fmt, *args):  # quieter journal
        if os.environ.get("ORBIS_WEB_VERBOSE"):
            super().log_message(fmt, *args)

    # ---- helpers
    def _hdrs(self, code: int, ctype: str, length: int, extra: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()

    def _json(self, code: int, obj, extra: dict | None = None):
        body = json.dumps(obj, default=str).encode()
        self._hdrs(code, "application/json", len(body), extra)
        self.wfile.write(body)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n > 1 << 20:
            raise ValueError("body too large")
        raw = self.rfile.read(n) if n else b"{}"
        d = json.loads(raw or b"{}")
        if not isinstance(d, dict):
            raise ValueError("expected a JSON object")
        return d

    def _gate(self) -> bool:
        self.st.last_request = time.monotonic()
        if not self.st.allowed_ip(self.client_address[0]):
            self._json(403, {"error": "address not allowed"})
            return False
        return True

    def _call(self, method: str, **params):
        try:
            return 200, {"ok": True, "result": self.st.client.call(method, **params)}
        except DaemonUnavailable as exc:
            return 503, {"ok": False, "error": str(exc)}
        except ApiError as exc:
            return 400, {"ok": False, "error": str(exc)}

    # ---- GET
    def do_GET(self):
        if not self._gate():
            return
        path = self.path.split("?", 1)[0]
        if path == "/api/v1/session":
            s = self.st.session(self.headers.get("Cookie"))
            return self._json(200, {"authenticated": bool(s), "csrf": s["csrf"] if s else None,
                                    "api": API_VERSION, "version": __version__,
                                    "token_configured": bool(self.st.token)})
        if path.startswith("/api/") or path.startswith("/v1/"):
            if path.startswith("/v1/"):
                return self._openai_proxy("GET")
            s = self.st.session(self.headers.get("Cookie"))
            if not s:
                return self._json(401, {"error": "login required"})
            if path.startswith("/api/v1/job/"):
                code, out = self._call("job", id=path.rsplit("/", 1)[-1])
                return self._json(code, out)
            route = GET_ROUTES.get(path)
            if not route:
                return self._json(404, {"error": "no such endpoint"})
            code, out = self._call(route[0])
            return self._json(code, out)
        return self._static(path)

    def _static(self, path: str):
        if path in ("", "/"):
            path = "/index.html"
        target = (self.st.ui_dir / path.lstrip("/")).resolve()
        if not str(target).startswith(str(self.st.ui_dir.resolve())) or not target.is_file():
            return self._json(404, {"error": "not found"})
        data = target.read_bytes()
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        self._hdrs(200, ctype, len(data))
        self.wfile.write(data)

    # ---- POST
    def do_POST(self):
        if not self._gate():
            return
        path = self.path.split("?", 1)[0]
        if path.startswith("/v1/"):
            return self._openai_proxy("POST")
        try:
            body = self._body()
        except (ValueError, json.JSONDecodeError) as exc:
            return self._json(400, {"error": str(exc)})
        if path == "/api/v1/login":
            ip = self.client_address[0]
            if not self.st.rate_ok(ip):
                return self._json(429, {"error": "too many attempts; wait a minute"})
            tok = str(body.get("token", ""))
            if not self.st.token or not hmac.compare_digest(tok.encode(), self.st.token.encode()):
                return self._json(401, {"error": "invalid token"})
            s = self.st.new_session()
            cookie = f"orbis_session={s['id']}; HttpOnly; SameSite=Strict; Path=/; Max-Age={SESSION_TTL}"
            return self._json(200, {"ok": True, "csrf": s["csrf"]}, {"Set-Cookie": cookie})
        s = self.st.session(self.headers.get("Cookie"))
        if not s:
            return self._json(401, {"error": "login required"})
        if not hmac.compare_digest(self.headers.get("X-Orbis-CSRF", ""), s["csrf"]):
            return self._json(403, {"error": "missing or bad CSRF token"})
        if path == "/api/v1/logout":
            self.st.sessions.pop(s["id"], None)
            return self._json(200, {"ok": True}, {"Set-Cookie": "orbis_session=; Max-Age=0; Path=/"})
        if path == "/api/v1/ai/chat":
            return self._chat(body)
        method = POST_ROUTES.get(path)
        if not method:
            return self._json(404, {"error": "no such endpoint"})
        code, out = self._call(method, **body)
        return self._json(code, out)

    # ---- AI
    def _llama_url(self) -> str:
        a = self.st.cfg.get("ai", {})
        return f"http://127.0.0.1:{int(a.get('port', 8081))}"

    def _chat(self, body: dict):
        msgs = body.get("messages")
        if not isinstance(msgs, list) or not msgs:
            return self._json(400, {"error": "messages required"})
        req = {"messages": msgs[-20:], "max_tokens": min(int(body.get("max_tokens", 256)), 1024),
               "temperature": float(body.get("temperature", 0.7)), "stream": False}
        try:
            r = urllib.request.urlopen(urllib.request.Request(
                self._llama_url() + "/v1/chat/completions", data=json.dumps(req).encode(),
                headers={"Content-Type": "application/json"}), timeout=600)
            data = json.loads(r.read())
        except urllib.error.HTTPError as exc:
            return self._json(502, {"error": f"model server: HTTP {exc.code}"})
        except (OSError, json.JSONDecodeError) as exc:
            return self._json(503, {"error": f"model server unavailable: {exc}"})
        return self._json(200, {"ok": True, "result": data})

    def _openai_proxy(self, verb: str):
        if not self.st.cfg.get("ai", {}).get("lan_api", False):
            return self._json(404, {"error": "LAN API disabled (orbis ai set lan_api true)"})
        auth = self.headers.get("Authorization", "")
        if not self.st.api_key or not hmac.compare_digest(auth.encode(), f"Bearer {self.st.api_key}".encode()):
            return self._json(401, {"error": "invalid API key"})
        if not self.path.startswith(("/v1/chat/completions", "/v1/completions", "/v1/models", "/v1/embeddings")):
            return self._json(404, {"error": "endpoint not proxied"})
        data = None
        if verb == "POST":
            n = int(self.headers.get("Content-Length") or 0)
            if n > 4 << 20:
                return self._json(413, {"error": "body too large"})
            data = self.rfile.read(n)
        try:
            r = urllib.request.urlopen(urllib.request.Request(self._llama_url() + self.path, data=data,
                                                              method=verb,
                                                              headers={"Content-Type": "application/json"}),
                                       timeout=900)
            payload = r.read()
            self._hdrs(r.status, r.headers.get("Content-Type", "application/json"), len(payload))
            self.wfile.write(payload)
        except urllib.error.HTTPError as exc:
            payload = exc.read()
            self._hdrs(exc.code, "application/json", len(payload))
            self.wfile.write(payload)
        except OSError as exc:
            self._json(503, {"error": f"model server unavailable: {exc}"})


class DualStackServer(ThreadingHTTPServer):
    daemon_threads = True


def _systemd_socket() -> socket.socket | None:
    if os.environ.get("LISTEN_PID") == str(os.getpid()) and int(os.environ.get("LISTEN_FDS", "0")) >= 1:
        return socket.socket(fileno=3)
    return None


def make_server(cfg: dict, client: Client | None = None, host: str | None = None, port: int | None = None):
    w = cfg.get("web", {})
    st = WebState(cfg, client or Client(), _read_secret(paths.etc_dir() / "web-token"),
                  _read_secret(paths.etc_dir() / "ai-api-key"))
    handler = type("BoundHandler", (Handler,), {"st": st})
    inherited = _systemd_socket()
    if inherited is not None:
        srv = DualStackServer(("127.0.0.1", 0), handler, bind_and_activate=False)
        srv.socket = inherited
        srv.server_address = inherited.getsockname()
    else:
        srv = DualStackServer((host or w.get("listen", "0.0.0.0"), int(port or w.get("port", 8420))), handler)
    return srv, st, inherited is not None


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--host")
    ap.add_argument("--port", type=int)
    a = ap.parse_args(argv)
    cfg = config.load_config()
    srv, st, activated = make_server(cfg, host=a.host, port=a.port)
    if not st.token:
        print("orbis-web: WARNING: no /etc/orbis/web-token; logins are impossible until one is created",
              file=sys.stderr)
    idle = float(cfg.get("web", {}).get("idle_exit_s", 600))
    if activated and idle > 0:
        def idle_watch():
            while True:
                time.sleep(15)
                if time.monotonic() - st.last_request > idle:
                    print("orbis-web: idle, exiting (socket activation will restart on demand)", file=sys.stderr)
                    os._exit(0)
        threading.Thread(target=idle_watch, daemon=True).start()
    print(f"orbis-web {__version__} serving on {srv.server_address}", file=sys.stderr, flush=True)
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
