# Mode Resource Budget

**Rule:** each personality runs what it needs, plus the shared necessities. Nothing in this document counts as freed memory unless it was **measured** after a transition. Every switch records a before/after snapshot in `/var/log/orbis/transitions.jsonl`.

## Memory model of the PS4 under Linux

- 8 GiB GDDR5 is shared by the CPU and GPU (the Pro's extra 1 GiB DDR3 is not exposed to Linux).
- The loader carves `vram.txt` MB out **before Linux starts**. Linux sees roughly 8 GiB − carve-out − firmware reservations; linux-psx measured about 6.8 GiB with a 1024 MB carve-out.
- Changing the carve-out **requires a reboot through the PS4 system software** and a payload relaunch.
- Vulkan on this APU also allocates from GTT (system RAM): RADV exposes 2/3 of (GTT + visible VRAM) as "device-local". GPU work costs Linux RAM regardless of the carve-out (GRAPHICS-REPORT §4).

## Services per mode

| | PC | GAMING | AI |
|---|---|---|---|
| systemd target | `orbis-pc.target` (+`graphical.target`) | `orbis-gaming.target` | `orbis-ai.target` |
| Runs | LightDM, Xorg (amdgpu DDX), XFCE session, PipeWire (user) | `orbis-gaming-session` on tty1: Xorg + Kodi (LAN-only images) or Steam Big Picture / ES-DE, optional openbox | `orbis-llama.service` (llama-server, CPU by default) |
| Must not run (verified after switch) | llama-server, Steam/gamescope/ES-DE session | display manager, XFCE, llama-server | **anything graphical**, and in CPU mode **any DRM client** |
| `vram.txt` preset (min / preferred) | 512 / 1024 MB | 1024 / 2048 MB | CPU: 32 / 128 MB · Vulkan: 512 / 1024 MB |
| Power preset | balanced | performance | balanced (quiet/performance selectable) |
| `vm.swappiness` | 60 | 20 | 10 (+ `vfs_cache_pressure=50`) |
| Readiness check | `display-manager.service` active | `orbis-gaming-session.service` active | `GET /health` on 127.0.0.1:8081 |

**Shared necessities (`multi-user.target`, every mode):** systemd + journald, udev, NetworkManager, sshd, orbisd, orbis-web (**socket-activated**: it exits after 10 idle minutes), zram swap, getty, and the LAN-only nftables policy (`orbis-lan-only.service`; a oneshot with no resident process).

**GAMING on LAN-only consoles:** Steam needs the internet to sign in and to download its runtime, so release images built LAN-only set `gaming.frontend = "kodi"`. Steam stays installed for consoles where `/etc/orbis/allow-internet` exists.

## Measured so far

These numbers are from the x86 build host, not the PS4. Python 3.13, idle, fixture root.

| Process | PSS | RSS | Notes |
|---|---|---|---|
| `orbisd` | **16.7 MiB** | 24.4 MiB | always on; owns the Unix socket and the alert monitor |
| `orbis-web` | **15.6 MiB** | 23.3 MiB | only while someone uses the dashboard |
| `orbis-ai-launch` | 0 while serving | — | exec()s llama-server; no resident Python |

Expect somewhat higher on Jaguar (same code, but Python on Arch differs slightly). The `orbis budget` command reports the live figures.

## Targets (to be confirmed on hardware)

These are **estimates** for idle "used" memory (`MemTotal − MemAvailable`) after the personality is ready. They are what `tests/hardware/qualify.sh` will measure and replace.

| Mode | Idle used target | Reasoning |
|---|---|---|
| AI (CPU, no model loaded) | ≤ 300 MiB | systemd + NetworkManager + sshd + orbisd ≈ 150–250 MiB on comparable minimal Arch systems |
| AI with a Q4_K_M 1.5B model, ctx 4096 | ≈ 300 + weights (~1.0 GiB) + KV (~0.1 GiB) + compute buffer | computed per model by `orbis ai plan` from GGUF metadata |
| PC (XFCE idle, no browser) | ≤ 750 MiB | XFCE + Xorg + PipeWire |
| GAMING (Steam Big Picture idle) | ≤ 1.1 GiB | Steam's CEF client dominates |
| GAMING (Kodi idle, LAN-only default) | ≤ 700 MiB | Kodi + Xorg; estimate |

## Switch strategy

| From → To | Typical method | Why |
|---|---|---|
| any → AI (CPU) | **fast** | needs ≥ 32 MB VRAM, which any boot has. Orbis stages `vram.txt=128` so the **next** payload launch returns ~896 MiB to Linux, and says so. |
| AI (booted at 128 MB) → PC / GAMING | **reboot** | 128 MB < 512/1024 MB minimum; staged `vram.txt` + `orbis.mode=` in `bootargs.txt`, then the operator relaunches the payload |
| PC ↔ GAMING (at ≥ 1024 MB) | **fast** | carve-out satisfies both |
| any → mode needing the GPU after a GPU fault | **reboot** | `amdgpu` VM fault / ring timeout / reset in the kernel log since boot |

**Policies:** `auto` (default), `fast` (refuses when a reboot is *required* unless `--force`), `reboot`.

**A fast switch is verification-gated.** After `systemctl isolate`:
1. The engine waits until no process or unit owned by another personality remains.
2. In CPU-AI mode it requires that no process holds `/dev/dri`.
3. It runs the readiness checks.

If anything is left over, the result is **not OK** and a clean reboot is offered. That is how "only when tested clean" is enforced on every switch, not just in a lab once.

**After an OS update:** `orbis-platform`'s upgrade hook only restarts `orbisd`. Personality services are pulled in by targets, never enabled directly, so an update cannot wake a dormant service.

## How to measure on a console

```sh
orbis budget                         # live: totals, slices, top PSS, DRM clients with VRAM/GTT
orbis mode ai --yes && orbis budget  # transition record includes memory_before/after/delta
jq '.memory_delta' /var/log/orbis/transitions.jsonl
tests/hardware/qualify.sh            # full round trip PC→AI→GAMING→PC with receipts
```
