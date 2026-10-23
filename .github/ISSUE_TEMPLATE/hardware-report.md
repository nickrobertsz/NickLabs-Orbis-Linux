---
name: Hardware report / bug
about: Report a result or a problem on a real PS4
---

**Console**: CUH-____ (Settings → System Information: southbridge ______ )
**Wi-Fi/BT chip** (if known): ______
**Orbis version**: `orbis --version` →
**Kernel**: `uname -r` →
**Mesa**: `pacman -Q mesa-orbis-ps4` →
**Mode** when it happened: PC / GAMING / AI
**VRAM** (`cat /boot/ps4/vram.txt`): ____ MB
**Display**: TV/monitor model, resolution, refresh

### What happened / what you expected

### Attach
- [ ] `orbis doctor --deep --json > doctor.json`
- [ ] `journalctl -b -k > kernel.log` (or the UDP mirror / netconsole capture if the console hung)
- [ ] for GPU/AI issues: `orbis-infer-harness report --out <campaign>` and `steps/<failing>/kmsg.log`
- [ ] last lines of `/var/log/orbis/transitions.jsonl` for mode-switch issues

Please remove anything personal (MAC/IP addresses, user names) before posting.
