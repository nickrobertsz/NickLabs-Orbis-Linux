# Bug Ledger

**Categories:**
- **VERIFIED-UPSTREAM-FIX**: a fix exists upstream with recorded hardware evidence.
- **OBSERVED**: an observed issue.
- **GAP**: an unverified compatibility gap.
- **HYPOTHESIS**: needs instrumentation.

An issue is never closed just because upstream marks it out of scope.

**Severity (release impact):**
- **S1** blocks the feature for everyone.
- **S2** blocks some hardware or configurations.
- **S3** degraded experience.
- **S4** cosmetic or documentation.

| ID | Category | Sev | Affected | Issue | Source / evidence | Orbis action | How to confirm | Status |
|---|---|---|---|---|---|---|---|---|
| DISP-001 | VERIFIED-UPSTREAM-FIX | S1 | CUH-12xx (MN864729), likely all fat | First userspace modeset blacks out the panel (VMUTE stranded behind 650 ms ICC delays) | aday1/ps4-hdmi-blackscreen@58ce86b; fix in feeRnt 6.15.4 crashnt-4.7 | use that tree (pinned) | modeset X11 → TTY → X11; `vbltest` | fix in source; HW untested by Orbis |
| DISP-002 | OBSERVED | S2 | all with 120 Hz-capable modelist | 1080p120 chosen on 60 Hz panels → black | aday1; DionKill issues.md; linux-psx 0011 | kernel patch 0001 + Xorg PreferredMode | `modetest -c` lists one mode | patch compile-checked (RELEASE-STATUS) |
| DISP-003 | OBSERVED | S3 | all | Non-CEA timings rejected ("attempted to set non-CEA mode") | aday1 | exact CEA VIC16 modeline | dmesg clean during DM start | config shipped |
| DISP-004 | HYPOTHESIS | S4 | MN864729 | `pre_enable` writes `HDCPEN_ENC_DIS` into `TSRST` | aday1 patch comment; feeRnt ps4_bridge.c:388 | none yet | A/B with aday1's `ps4_bridge_fix_hdcpen` knob | open |
| DISP-005 | GAP | S3 | all | Hotplug/EDID not supported (hard-coded modes) | DionKill issues.md; aday1 (EDID 0 bytes) | headless-first AI mode; recovery via bootargs | — | accepted limitation |
| BOOT-001 | OBSERVED | S1 | all | Kernel with boot protocol 2.13 hard-locked the console | aday1 table | `orbis boot check-kernel` refuses 2.13, warns on unknown, requires XLF_KERNEL_64 and `CONFIG_X86_PS4`; runs in the pacman kernel hook and the image builder | tested on synthetic headers + real crashnt-4.7 bzImage (2.15, X86_PS4=y) | implemented |
| BOOT-002 | OBSERVED | S2 | all | Wrong initramfs (internal vs external, southbridge script) → `mount /newroot failed` | DionKill issues.md | Orbis builds one initramfs from pinned source; `root=LABEL=ORBIS-ROOT` | first boot | implemented, HW untested |
| BOOT-007 | OBSERVED | S1 | consoles with an older community install | initramfs-ps4@c21a720 mounts `LABEL=psxitarch`, then the internal `/ps4hdd/app/linux.img`, and only then honours `root=`: an existing install boots instead of the requested root | GayCorp init (read 2026-10-26) | Orbis patch 0002: explicit `root=` wins; waits 30 s for it + fsck; discovery only without `root=` | `packaging/initramfs/tests/root-selection.sh` (5 cases, runs in every build) | fixed in Orbis initramfs; HW untested |
| BOOT-008 | OBSERVED | S2 | Aeolia/Belize | initramfs-ps4 decrypts and mounts the PS4 internal drive on every boot and its hook moves `linux.img` on it | GayCorp init + hooks/ps4hdd_open | with `root=`, opened only on `orbis.ps4hdd=1` (brief: no changes to existing disks without explicit choice) | root-selection check | fixed in Orbis initramfs |
| BOOT-009 | OBSERVED | S2 | all | The loader looks up each of bzImage, initramfs, bootargs.txt, vram.txt **separately** (usb0, usb1, /data/linux/boot, /user/system/boot): a missing file on USB silently comes from the internal drive | ps4-linux-loader@d0f4d4c main-aio.c | Orbis images always carry all four files; README-ORBIS.txt and INSTALL-NOBARA say why | — | implemented |
| BOOT-010 | OBSERVED | S3 | all | The community v1.0 initramfs init is hard-wired to `mount LABEL=psxitarch`; it is a distro-specific init, not a general one | X-F1REBALL-X v1.0 initramfs | used only as a firmware source; never its scripts | build manifest `firmware_source` | implemented |
| BOOT-003 | OBSERVED | S2 | all | UAS-less USB-SATA adapters not found by newer kernels; hubs hide drives | DionKill issues.md | doctor warns on BOT (usb-storage) root and on root behind a hub | `orbis doctor` | implemented |
| BOOT-004 | GAP | S2 | multi-USB setups | Loader takes the first USB with a bzImage; several FAT partitions = ambiguity | ps4-linux-loader main-aio.c | doctor warns when >1 FAT partition holds a bzImage | — | implemented |
| BOOT-005 | GAP | S3 | all | Reboot returns to PS4 system software; payload must be relaunched | DionKill issues.md; loader design | engine states it in every reboot plan; capability `power.reboot_to_linux` UNSUPPORTED | — | accepted limitation |
| BOOT-006 | GAP | S2 | all | Kernel/module mismatch after manual kernel swaps | linux-psx BOOTING.md | doctor FAILs if `/usr/lib/modules/$(uname -r)` missing; kernel package ships both together | — | implemented |
| MEM-001 | OBSERVED | S2 | all | VRAM is a boot-time carve-out (vram.txt); cannot change at runtime | ps4-linux-loader; linux-psx PS4-DYNAMIC-MEMORY.md | per-mode presets staged in vram.txt; reboot only when the carve-out is too small | fast/reboot plan tests | implemented |
| MEM-002 | HYPOTHESIS | S3 | all | RADV exposes GTT as device-local on Liverpool (APU) → Vulkan allocations consume Linux RAM | Mesa 26.2.5 `radv_physical_device_init_mem_types` | AI budget counts GTT; harness samples `mem_info_gtt_used` | heap sizes from `orbis-gpu-probe --vk` on HW | open |
| MEM-003 | GAP | S2 | all | Long inference can exhaust shared RAM | brief | MemAvailable alerts (PSI absent), `MemoryHigh=92%` on orbis-ai.slice, `OOMScoreAdjust=500`, ctx capped from measured memory | `orbis ai plan` | implemented |
| MEM-004 | OBSERVED | S3 | all | 32/64 MB VRAM caused display problems for some users | DionKill payloads.md | presets start at 128 MB; AI CPU uses 128 | — | implemented |
| GPU-001 | OBSERVED | S1 | some configs | "amdgpu" loaded but GL runs on llvmpipe | brief; community reports | `gpu.gl.hw` = renderer + pixel test; dashboard/doctor FAIL | validated on llvmpipe host | implemented |
| GPU-002 | OBSERVED | S1 | Baikal B1 (upstream) | Vulkan depth/cube hangs; FLAT fill path; HTILE | linux-psx MESA-TESTING.md | import 0005; Vulkan graphics not advertised | probe compute fill tests at 4 KiB threshold | patch imported; HW untested |
| GPU-003 | OBSERVED | S1 | one PS4 (Carlos Georges) | Long-prefill GPU fault during Vulkan inference; recovery failed | carlosgeorges.com/notes/ps4-linux.html | harness + gate; Vulkan BLOCKED until gate | campaign on HW | open, root cause unknown (H1–H6) |
| GPU-004 | HYPOTHESIS | S3 | all GFX7 | No fp16 arithmetic → llama.cpp uses fp32-arith variants; numerical drift | Mesa source; Carlos Georges (rounding) | harness tensor/KLD comparison | campaign | open |
| GPU-005 | OBSERVED | S3 | all | `RADV_DEBUG=nocompute` advice is obsolete (not in Mesa docs); `RADV_QUEUE_DISABLE=compute` exists | DionKill issues.md; Mesa envvars docs | no global env vars; probe tests queue families separately | — | implemented |
| GPU-006 | GAP | S2 | all | GPU reset success unknown on PS4 | — | after any fault, GPU modes require a clean reboot | — | implemented policy |
| GPU-007 | OBSERVED | S3 | all | UVD handles exhausted / runtime closes under browser load (experimental UVD) | linux-psx MESA-TESTING.md | UVD not shipped; software decode | — | avoided |
| PKG-001 | OBSERVED | S1 | Arch-based | `pacman -Syu` / `-S mesa` replaces PS4 Mesa | DionKill distrodiy.md | pre-transaction guard hooks + one-shot logged override | unit tests | implemented |
| PKG-002 | OBSERVED | S2 | community packages | Community mesa-ps4 built from git HEAD; unsigned; PKGBUILD/binary version mismatch | DionKill repo | pinned 26.2.5 tarball + sha256 | — | replaced |
| PKG-003 | OBSERVED | S1 | CachyOS v3/v4 repos | x86-64-v3 binaries SIGILL on Jaguar | Jaguar ISA (no AVX2/FMA/BMI2) | `orbis isa-audit --pacman`, binary audit, PKGBUILD flags | detects a -march=x86-64-v3 test binary; passes btver2/baseline | implemented |
| LIC-001 | OBSERVED | S2 | prebuilt community kernels | "no-built-in-fw" kernel still embeds MT7668 blobs with no licence; rmux 7.1.7 embeds Marvell+MediaTek | feeRnt config; linux-psx rmux-reference | `CONFIG_EXTRA_FIRMWARE=""`; bzImage checker warns | real crashnt-4.7 binary flagged | implemented |
| LIC-002 | OBSERVED | S2 | all | Liverpool GPU firmware comes from a private repo; redistribution unknown | initramfs-ps4 CI | never in source/redistributable archives; taken from a verified community initramfs per profile and hash-checked; firmware-carrying images marked `redistributable: false` | 17 build_initramfs tests | implemented |
| LIC-003 | GAP | S3 | Marvell consoles | Marvell blobs not yet proven identical to linux-firmware (kernel.org unreachable from the Orbis host); two community copies agree | references-and-loader-2026-10-26.md | treated as operator-supplied; `release.sh` writes `firmware-provenance.json` against Arch `linux-firmware-marvell` | provenance report on Nobara | open |
| FW-001 | OBSERVED | S3 | all (loader ≥ v24b) | The loader prepends its own cpio (HDD key, EDID, 8 GPU microcode files from the console); same-path files in the initramfs override them; UVD/VCE never extracted | ps4-kexec-common firmware.c/kexec.c | profiles keep Orbis' verified copies authoritative; `firmware-free` profile relies on the loader (no UVD/VCE, no Wi-Fi) | stage-0 report lists firmware hashes | documented |
| NET-001 | VERIFIED-UPSTREAM-FIX | S2 | CUH-1215/1216 | Marvell Torus 2 Wi-Fi errors on default kernels | feeRnt README | feeRnt tree carries the fixes; `sd8897_uapsta.bin` required by the cuh-1215a profile (mwifiex is built in, so it must be in the initramfs) | Wi-Fi association | HW untested |
| NET-003 | GAP | S2 | NickLabs consoles | Consoles must stay off the public internet while reaching the LAN | Nick 2026-10-26 | `orbis-lan-only.service` (nftables, before network-pre) + doctor `network.lan_only`; Kodi as offline GAMING frontend; LAN NTP; `orbis-update --orbis-only` | doctor + `curl` check in qualify | implemented, HW untested |
| NET-002 | OBSERVED | S3 | Baikal (linux-psx) | Native Ethernet named `psx0`; not matched by en*/eth* rules | linux-psx ETHERNET/BOOTARGS | NetworkManager drop-in | — | implemented |
| INP-001 | OBSERVED | S3 | all | DS4 must be re-paired every boot | DionKill issues.md | documented; USB recommended | — | accepted limitation |
| THR-001 | GAP | S2 | all | No fan control/readout exposed | linux-psx README | shown as UNSUPPORTED; no fake values | — | accepted limitation |
| PWR-001 | GAP | S1 | all | Suspend/hibernate unsafe/unbuilt | kernel configs | sleep targets masked; logind ignores suspend keys; sleep.conf disables | — | implemented |
| UPD-001 | OBSERVED | S3 | non-EFI BLS systems | bootc misreports status on non-EFI BLS systems | bootc#2375 | relevant to Fedora track only | — | tracked |
| TOOL-002 | OBSERVED | S4 | build hosts without mtools | FAT step of the image builder needed mtools (unavailable on the Orbis host) | RELEASE-STATUS 0.1.0 | built-in deterministic FAT32 writer + read-back + fsck.fat; mcopy optional | 8 image tests; fsck clean | fixed |
| TOOL-003 | OBSERVED | S1 | first boot | Images had no usable login (user locked, `%wheel` sudo commented out in Arch) | review 2026-10-26 | build requires an SSH key and/or password hash; wheel sudo; one-shot `sudo passwd orbis` | build-rootfs refuses without credentials | fixed |
| TOOL-001 | OBSERVED | S4 | x86 host, llama.cpp b11845 | `llama-perplexity --kl-divergence` intermittently exits 0 after the first chunk without statistics (~5% direct, ~30% under a wrapper) | Orbis host runs | harness retries and classifies `TOOL_ERROR` (never a GPU verdict) | — | worked around; upstream report pending |
