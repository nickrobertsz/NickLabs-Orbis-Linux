# Personalities

| Mode | Target | What runs | What must not run |
|---|---|---|---|
| PC | `orbis-pc.target` | LightDM + XFCE (X11, radeonsi), user apps | llama-server, Steam/gamescope session |
| GAMING | `orbis-gaming.target` | `orbis-gaming-session.service` on tty1: X11 + Steam Big Picture / ES-DE / Kodi | display manager, llama-server |
| AI | `orbis-ai.target` | `orbis-llama.service` (llama-server, CPU by default), headless | anything graphical, any DRM client in CPU mode |

Shared in every mode (multi-user.target): NetworkManager, sshd, orbisd, orbis-web.socket, journald, zram.

Profiles: `platform/config/profiles/*.toml`. Desktop shortcuts: `modes/pc/*.desktop`.
The GAMING session launcher is `platform/bin/orbis-gaming-session` / `orbis-gaming-xinitrc`.
The AI launcher is `platform/orbis/ai_launch.py` (computes safe limits at start, then execs llama-server).
