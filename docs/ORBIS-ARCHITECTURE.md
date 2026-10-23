# Orbis Architecture

```text
 PS4 system software ──(GoldHEN payload: ps4-linux-loader)──► reads USB FAT "ORBIS-BOOT":
                                                               bzImage · initramfs.cpio.gz · bootargs.txt · vram.txt
   │ kexec, E820 carve-out = vram.txt, hardware_subarch = PS4
   ▼
 linux-orbis-ps4 (feeRnt 6.15.4 crashnt-4.7 + Orbis patches, firmware-free)
   ▼
 Orbis initramfs (initramfs-ps4 source, pinned; Liverpool firmware supplied by the operator)
   │ root=LABEL=ORBIS-ROOT
   ▼
 systemd ── orbis-mode-generator: default.target := orbis-<mode>.target
   │           (precedence: systemd.unit= > orbis.mode= > /var/lib/orbis/desired-mode > /etc/orbis/default-mode > pc)
   ├─ multi-user.target  (shared): NetworkManager · sshd · journald · zram · orbisd · orbis-web.socket
   ├─ orbis-pc.target     → graphical.target → LightDM/Xorg/XFCE
   ├─ orbis-gaming.target → orbis-gaming-session (tty1: Xorg + Steam BPM / ES-DE / Kodi)
   └─ orbis-ai.target     → orbis-llama.service (orbis-ai-launch → exec llama-server)
```

## Components

| Component | Language | Privilege | Purpose | Why this language |
|---|---|---|---|---|
| `orbis` CLI | Python (stdlib only) | caller | status, mode, plan, doctor, capabilities, budget, boot, isa-audit, ai, pkg | Python is already in the base image; no new runtime |
| `orbisd` | Python | root, **Unix socket only** (`/run/orbis/orbisd.sock`, 0660 root:orbis) | mode engine, diagnostics jobs, alerts monitor | ~17 MiB PSS measured; development speed; one process |
| `orbis-web` | Python | user `orbis-web` (groups `orbis`, `orbis-admin`), socket-activated, exits when idle | LAN dashboard + API gateway + optional OpenAI-compatible proxy | ~16 MiB PSS while in use, 0 when idle; no browser runtime on the device |
| `orbis-gpu-probe` | C (dlopen EGL/Vulkan) | caller | measured GPU tiers, compute correctness | must survive missing or broken GL/Vulkan libraries; bounded waits; child process, so a GPU hang can't wedge orbisd |
| `orbis-infer-harness` | Python | caller (root for /dev/kmsg) | CPU-vs-Vulkan llama.cpp campaigns, boundary bisection, gate receipts | orchestration; crash-resumable journal |
| `orbis-mode-generator` | POSIX sh | systemd generator | pick the boot personality | runs before most of userspace |
| `orbis-ai-launch` | Python → exec | `orbis-ai` user | compute safe llama.cpp limits from measured state, then exec | zero resident overhead |
| `orbis-pkg-guard` | Python | pacman hook | protect the PS4 graphics/kernel stack | — |
| `build_initramfs.py` | Python | root at install | reproducible newc initramfs, operator firmware with hash checks | no cpio dependency; deterministic |
| `make_usb_image.py` | Python | builder | MBR + FAT32 + ext4 image without loop mounts; guarded device writer | — |

**Language choice:** Rust was considered for orbisd. It would save roughly 10–12 MiB of RSS. It was not chosen for v1 because the platform logic is mostly orchestration and parsing, where Python's iteration speed and readability for contributors matter more. The C probe is the only component where a crash, hang or missing library must not take the caller down.

## Platform-layer modules (`platform/orbis/`)

| Module | Responsibility |
|---|---|
| `paths` | every path re-rooted under `ORBIS_ROOT`, so all logic is testable against fixture trees |
| `config` | packaged defaults → `/etc/orbis/orbis.toml` → API-settable allow-listed keys (`/var/lib/orbis/settings.json`); profiles |
| `hardware` | read-only inventory: Sony/AMD PCI IDs, Jaguar ISA level, meminfo, amdgpu memory counters, DRM connectors, hwmon, network, root storage transport (UAS/BOT/hub), kernel config |
| `capabilities` | static matrix (per qualification group) + runtime overlays + Orbis policy |
| `bzimage` | x86 setup-header and loader-contract checks; embedded IKCONFIG extraction |
| `bootfs` | loader files: vram.txt semantics mirrored from `main-aio.c`, bootargs editing, kernel-pair install/rollback |
| `systemd` | `RealSystemd` (systemctl/loginctl with timeouts) and `FakeSystemd` (in-memory model for tests and `--simulate`) |
| `engine` | plan (fast/reboot/noop/blocked with reasons) → execute (persist, stage boot files, end sessions, isolate, verify residue, apply sysctl/power, readiness, measure, record) |
| `memory` | snapshots, PSS, DRM per-client VRAM/GTT (fdinfo), slices, residue detection, alert levels (no PSI) |
| `gpu` | probe runner, renderer/device classification, kernel-log fault scan, tier summary, gate receipt |
| `ai` | GGUF metadata reader, KV/compute estimates, launch plan, Vulkan admission by gate |
| `isa_audit` | pacman arch/repo audit, ELF ISA notes, disassembly density classifier |
| `pkgguard` | hook policy + one-shot override |
| `doctor` | the support report (text/JSON) |
| `daemon`, `client`, `web` | API v1 over the Unix socket; HTTP gateway |

## API v1 (orbisd)

Newline-delimited JSON over `/run/orbis/orbisd.sock`. Unknown methods are rejected. Callers are identified with `SO_PEERCRED`.

| Read (group `orbis`) | Write (group `orbis-admin` or root) |
|---|---|
| `hello`, `status`, `plan`, `job`, `capabilities`, `budget`, `ai.status`, `ai.models`, `logs`, `gaming.status`, `doctor.last` | `switch` (needs `confirm_sessions` / `confirm_reboot` when relevant), `doctor.run`, `ai.set`, `settings.set` (allow-listed keys), `reboot` (`confirm=true`) |

HTTP (orbis-web) mirrors these under `/api/v1/…`:
- login with the dashboard token;
- session cookie (HttpOnly, SameSite=Strict) plus an `X-Orbis-CSRF` header on every POST;
- private/loopback source addresses only;
- strict CSP, so the UI loads nothing from the internet.

`/v1/*` (OpenAI-compatible) is proxied only when `ai.lan_api = true`, with a separate bearer key. llama-server itself binds to 127.0.0.1.

## Data layout (one installation)

| Path | Content |
|---|---|
| `/etc/orbis/orbis.toml` | admin config (pacman `backup`) |
| `/etc/orbis/web-token`, `ai-api-key` | secrets (0640 root:orbis-web), generated at install |
| `/var/lib/orbis/desired-mode` | one word, read by the generator |
| `/var/lib/orbis/settings.json` | dashboard-changed settings |
| `/var/lib/orbis/models/*.gguf` (+ `.orbis.json`) | model store (group orbis-admin, setgid) |
| `/var/lib/orbis/gpu-gate.json` | inference gate receipt |
| `/var/log/orbis/transitions.jsonl`, `pkg-guard.log` | audit trail |
| `/boot/ps4` | the PS4 loader FAT partition (`ORBIS-BOOT`) |
| `/usr/share/orbis/` | profiles, capability matrix, firmware manifest, UI, initramfs source |

## Package strategy

**Base (all modes):**
- Arch `base`, systemd, NetworkManager, OpenSSH, zram-generator, Python;
- `linux-orbis-ps4`, `mesa-orbis-ps4` (+lib32), `orbis-platform`.

**Personality packages (installed in advance, inert until their target is active):**
- XFCE/LightDM/PipeWire/Firefox for PC;
- Steam/openbox/Wine for GAMING;
- `llama-cpp-orbis` for AI.

systemd-sysext and OCI containers were considered for the personality packages and not used in v1:
- **sysext** would need `CONFIG_EROFS_FS` or squashfs for images (both disabled in the PS4 kernels), plus merge/unmerge on every switch. It saves disk space, not RAM, and disk space is not the constraint.
- **OCI containers** would mean a second userspace per mode, which the brief rules out.

## Security notes

- orbisd never listens on the network.
- orbis-web holds no privileges; every action goes through the narrow orbisd API.
- systemd's `IPAddressDeny` and `DevicePolicy` need `CONFIG_CGROUP_BPF`, which the PS4 kernels don't enable. Orbis therefore relies on orbis-web's own address filter and on unit sandboxing (`ProtectSystem=strict`, `NoNewPrivileges`, etc.).
- The inference service runs as `orbis-ai` with read-only models and `OOMScoreAdjust=500`.
