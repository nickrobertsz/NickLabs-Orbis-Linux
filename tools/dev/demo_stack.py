#!/usr/bin/env python3
"""Run orbisd + orbis-web against a fixture PS4 root and an in-memory systemd.

For UI development, screenshots and integration tests on any Linux/macOS box.
Nothing on the host is changed. Prints the URL and token.
"""
import argparse
import os
import sys
import tempfile
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "platform"))
sys.path.insert(0, str(REPO / "tests" / "unit"))


def start(port: int = 0, token: str = "demo-token-0000"):
    from helpers import FixtureRoot
    fx = FixtureRoot().__enter__()
    fx.boot_partition(vram="1024", bootargs="pci=nocrs intremap=off root=LABEL=ORBIS-ROOT rw orbis.mode=pc")
    (fx.root / "etc/orbis/web-token").write_text(token + "\n")
    (fx.share / "gaming-compat.json").write_text((REPO / "manifests/gaming-compat.json").read_text())
    from orbis import config, daemon, memory, web
    from orbis.client import Client
    from orbis.systemd import default_fake
    sd = default_fake()
    sd.start("orbis-pc.target")
    cfg = config.load_config()
    cfg["orbis"]["stop_timeout_s"] = 0.2
    sock = Path(tempfile.mkdtemp()) / "orbisd.sock"
    st = daemon.State(sd=sd, cfg=cfg, monitor=False, engine_kwargs=dict(
        sleep=lambda s: None, http_ok=lambda u, t: True, gpu_state=lambda: (True, {}),
        vram_reader=lambda: 1024, residue_fn=lambda p, i: [], drm_clients_fn=lambda: [],
        snapshot=lambda: memory.snapshot(include_processes=False)))
    st.alert("warning", "demo", "fixture data: this is not a PS4")
    dsrv = daemon.make_server(sock, st)
    threading.Thread(target=dsrv.serve_forever, daemon=True).start()
    wsrv, wst, _ = web.make_server(cfg, client=Client(sock), host="127.0.0.1", port=port)
    threading.Thread(target=wsrv.serve_forever, daemon=True).start()
    return fx, dsrv, wsrv, sd


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8420)
    a = ap.parse_args()
    fx, d, w, sd = start(a.port)
    print(f"Orbis demo dashboard: http://127.0.0.1:{w.server_address[1]}/  token: demo-token-0000", flush=True)
    threading.Event().wait()
