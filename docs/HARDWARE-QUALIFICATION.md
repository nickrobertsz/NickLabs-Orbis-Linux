# Physical Console Qualification — what is still required

Everything here needs the **CUH-1215A** in front of you. Nothing in this list has been done. Until a step's evidence comes back, the matching capability stays **UNVERIFIED** in HARDWARE-MATRIX and RELEASE-STATUS.

Send each piece of evidence back as files: the `orbis-logs/` text files, the receipts JSON, or photos of the screen where noted.

## Q0. Stage 0: before any root filesystem (≈ 10 min, USB stick)

Image: `orbis-0.2.0-stage0-cuh-1215a.img` (INSTALL-NOBARA §3).

| # | Check | Evidence | Pass criterion |
|---|---|---|---|
| Q0.1 | GoldHEN BinLoader accepts the payload (`linux-1024mb.elf`; `.bin` from `make loader-payload` if not) | your note | Linux starts |
| Q0.2 | Kernel boots with the Orbis 6.15.4 build (loader contract: protocol 2.15, XLF 0x23) | `stage0-001.txt` header | `kernel: Linux … 6.15.4-orbis-ps4…` |
| Q0.3 | Southbridge and GPU are what Nick stated | `== southbridge`, `== pci` | Belize; `1002:9920`-family Liverpool |
| Q0.4 | Orbis init patch: USB root search, no internal-drive access | screen / dmesg | no "ps4hdd" activity without `orbis.ps4hdd=1` |
| Q0.5 | amdgpu loads all firmware | `stage0-001-dmesg.txt` | no `failed to load firmware`, no ring test failure |
| Q0.6 | HDMI picture at 1080p60 from the initramfs console | photo + `== drm` | `HDMI-A-1 status=connected`, mode list contains 1920x1080 |
| Q0.7 | Wi-Fi/BT chip and firmware | `== network interfaces`, dmesg `mwifiex` | `mwifiex_sdio` loads `mrvl/sd8897_uapsta.bin` (if it asks for sd8797, the chip is Torus 1) |
| Q0.8 | Loader-provided EDID and key | `loader EDID`, `loader HDD key: present (not read)` | present |
| Q0.9 | USB storage enumerates and the FAT write works | the report exists at all | `orbis-logs/stage0-001.txt` on the stick |

## Q1. Full system boot (USB 3 SSD)

| # | Check | Evidence | Pass criterion |
|---|---|---|---|
| Q1.1 | Root on `LABEL=ORBIS-ROOT` mounts and systemd starts | photo / `journalctl -b` | login prompt or LightDM |
| Q1.2 | Login works (SSH key and/or initial password, forced change) | your note | `ssh orbis@<ip>` succeeds |
| Q1.3 | `sudo orbis doctor --deep` | `doctor.json` (inside the receipt) | no FAIL; record every INFO/WARN |
| Q1.4 | Hardware OpenGL, not llvmpipe | doctor `gpu.gl.hw` | `PASSED_ON_THIS_DEVICE` |
| Q1.5 | Vulkan compute correctness suite (RADV on Liverpool) | doctor `--deep` | 15/15 on the device, or a precise failure list |
| Q1.6 | LAN-only policy active | doctor `network.lan_only`; `curl -m5 https://example.com` fails; the dashboard works from Nobara | OK + blocked + reachable |
| Q1.7 | Wi-Fi associates to the NickLabs LAN (or Ethernet up) | `nmcli dev` | connected |
| Q1.8 | Time via LAN NTP (INSTALL-NOBARA §6) | `timedatectl` | synchronized |

## Q2. Personalities and switching

| # | Check | Evidence | Pass criterion |
|---|---|---|---|
| Q2.1 | `sudo orbis-qualify --model CUH-1215A` (PC→AI→GAMING→PC) | `/var/lib/orbis/receipts/*.json` | every switch OK; memory deltas recorded |
| Q2.2 | HDMI picture after each switch, including X11 → TTY → X11 | your note per switch | picture every time (DISP-001 regression check) |
| Q2.3 | A reboot-type switch (AI at 128 MB → PC) stages `vram.txt`/`bootargs.txt`; payload relaunch lands in PC | `transitions.jsonl` + note | lands in the staged mode |
| Q2.4 | GAMING: Kodi starts on tty1; controller works over USB | note / photo | usable |
| Q2.5 | HDMI audio | note | sound |
| Q2.6 | AI (CPU): a 0.5B–1.5B GGUF answers through the dashboard chat | `orbis ai plan`, a screenshot | answers; measured tok/s recorded |

## Q3. GPU inference investigation (only after Q1–Q2 pass)

| # | Check | Evidence | Pass criterion |
|---|---|---|---|
| Q3.1 | Inference harness campaign, with `receive` running on Nobara | campaign directory + received kmsg | report classifies every boundary; no guessing |
| Q3.2 | Gate receipt | `gpu-gate.json` | Vulkan AI enabled only if the gate passes |

## Q4. Recovery paths

| # | Check | Pass criterion |
|---|---|---|
| Q4.1 | Edit `bootargs.txt` on Nobara to `orbis.mode=ai` | boots headless |
| Q4.2 | `orbis boot rollback-kernel` after a staged kernel change | previous kernel boots |
| Q4.3 | `orbis-update --orbis-only --from http://<nobara>:8780/` with a rebuilt package | upgrade applies; doctor OK |

## Not in this qualification

- CUH-2215B (Baikal) needs its own target and profile first.
- 4K, Pro and Gladius are out of scope for this console.
- Suspend, fan control and DS4 re-pairing are UNSUPPORTED by design or upstream (HARDWARE-MATRIX).
