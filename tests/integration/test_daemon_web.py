"""orbisd + orbis-web end to end over real sockets (fixture root, in-memory systemd)."""
import http.cookiejar
import json
import sys
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools" / "dev"))
sys.path.insert(0, str(REPO / "tests" / "unit"))
import demo_stack  # noqa: E402


class DaemonWebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fx, cls.d, cls.w, cls.sd = demo_stack.start(0, token="t0ken")
        cls.base = f"http://127.0.0.1:{cls.w.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.w.shutdown()
        cls.d.shutdown()
        cls.fx.__exit__(None, None, None)

    def opener(self):
        return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def req(self, op, path, body=None, csrf=None):
        data = json.dumps(body).encode() if body is not None else None
        r = urllib.request.Request(self.base + path, data=data, method="POST" if data is not None else "GET",
                                   headers={"Content-Type": "application/json", **({"X-Orbis-CSRF": csrf} if csrf else {})})
        try:
            with op.open(r, timeout=30) as resp:
                return resp.status, json.loads(resp.read()), resp.headers
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}"), e.headers

    def login(self):
        op = self.opener()
        code, d, h = self.req(op, "/api/v1/login", {"token": "t0ken"})
        self.assertEqual(code, 200)
        self.assertIn("HttpOnly", h["Set-Cookie"])
        self.assertIn("SameSite=Strict", h["Set-Cookie"])
        return op, d["csrf"]

    def test_static_ui_and_security_headers(self):
        op = self.opener()
        with op.open(self.base + "/", timeout=10) as r:
            body = r.read().decode()
            self.assertIn("PS4 ORBIS LINUX", body)
            self.assertIn("default-src 'self'", r.headers["Content-Security-Policy"])
            self.assertEqual(r.headers["X-Frame-Options"], "DENY")
        code, _, _ = self.req(op, "/../../etc/passwd")
        self.assertEqual(code, 404)

    def test_auth_required_and_bad_token(self):
        op = self.opener()
        self.assertEqual(self.req(op, "/api/v1/status")[0], 401)
        self.assertEqual(self.req(op, "/api/v1/login", {"token": "nope"})[0], 401)

    def test_csrf_required_for_changes(self):
        op, csrf = self.login()
        self.assertEqual(self.req(op, "/api/v1/plan", {"mode": "ai"})[0], 403)
        code, d, _ = self.req(op, "/api/v1/plan", {"mode": "ai"}, csrf)
        self.assertEqual(code, 200)
        self.assertEqual(d["result"]["method"], "fast")

    def test_status_capabilities_and_switch_round_trip(self):
        op, csrf = self.login()
        code, d, _ = self.req(op, "/api/v1/status")
        self.assertEqual(code, 200)
        st = d["result"]
        self.assertEqual(st["current_mode"], "pc")
        self.assertTrue(st["platform"]["is_ps4"])
        self.assertEqual(set(st["profiles"]), {"pc", "gaming", "ai"})
        code, d, _ = self.req(op, "/api/v1/capabilities")
        self.assertEqual(code, 200)
        self.assertIsNone(d["result"]["group"])          # Belize without a declared model
        # Switch PC -> AI through the dashboard.
        code, d, _ = self.req(op, "/api/v1/switch", {"mode": "ai", "confirm_sessions": True}, csrf)
        self.assertEqual(code, 200)
        jid = d["result"]["job"]
        for _ in range(100):
            code, j, _ = self.req(op, f"/api/v1/job/{jid}")
            if j["result"]["state"] != "running":
                break
            time.sleep(0.05)
        res = j["result"]["result"]
        self.assertTrue(res["ok"], res)
        self.assertEqual(self.sd.is_active("orbis-ai.target"), "active")
        self.assertEqual(self.sd.is_active("display-manager.service"), "inactive")
        code, d, _ = self.req(op, "/api/v1/logs")
        self.assertEqual(d["result"]["transitions"][-1]["plan"]["to_mode"], "ai")
        # And back.
        code, d, _ = self.req(op, "/api/v1/switch", {"mode": "pc", "confirm_sessions": True}, csrf)
        jid = d["result"]["job"]
        for _ in range(100):
            code, j, _ = self.req(op, f"/api/v1/job/{jid}")
            if j["result"]["state"] != "running":
                break
            time.sleep(0.05)
        self.assertTrue(j["result"]["result"]["ok"])
        self.assertEqual(self.sd.is_active("orbis-pc.target"), "active")

    def test_reboot_needs_confirmation(self):
        op, csrf = self.login()
        code, d, _ = self.req(op, "/api/v1/reboot", {}, csrf)
        self.assertEqual(code, 400)
        self.assertFalse(self.sd.rebooted)

    def test_lan_openai_api_disabled_by_default(self):
        op = self.opener()
        code, d, _ = self.req(op, "/v1/models")
        self.assertEqual(code, 404)

    def test_unknown_daemon_method_rejected(self):
        from orbis.client import ApiError, Client
        c = Client(self.d.server_address)
        with self.assertRaises(ApiError):
            c.call("rm_rf_slash")
        self.assertEqual(c.call("hello")["api"], 1)


if __name__ == "__main__":
    unittest.main()
