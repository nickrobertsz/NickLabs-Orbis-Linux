# Upstream Credits and Licence Inventory

Orbis is an integration project. Its PS4 support stands on years of community work. Nothing listed here is presented as an Orbis invention.

## People and projects

| Project / person | What Orbis uses or learned from | Where |
|---|---|---|
| **feeRnt** — `ps4-linux-12xx`; with **crashniels**, **rancido**, **Ps3itaTeam** (per aday1's credits) | the 6.15.4 crashnt-4.7 kernel tree, including the ps4_bridge ICC-delay fix and the Marvell Torus Wi-Fi fixes | `kernel/targets/aeolia-belize-6.15.json` |
| **aday1** — `ps4-hdmi-blackscreen` | root-cause analysis of the HDMI black screen, the boot-protocol hazard table, diagnostic tools (`vbltest`, `fliponly`, `wlmirror`) | BUG-LEDGER DISP-001/004, BOOT-001; `orbis.bzimage` |
| **meerzulee** and Linux psX / Omarchy psX contributors | Baikal 7.2 kernel; the Mesa 26.2.5 patch series (0001 port, 0003, 0005) with provenance; dynamic-memory research; firmware manifests and hashes; experiment discipline | `graphics/patches/mesa/`, `manifests/firmware.json`, `kernel/targets/baikal-7.2.json` |
| **froyo** (author of the DionKill Mesa patch) and **centi_07** (PKGBUILDs) | original Liverpool/Gladius Mesa enablement; xf86-video-amdgpu cursor fix | `graphics/patches/*/provenance.json` |
| **DionKill** and tutorial contributors — `ps4-linux-tutorial`, `ps4-video-archlinux` | the community knowledge base: issues, payload/VRAM behaviour, distro landscape, package workflow | DISTRO-DECISION, HARDWARE-MATRIX, BUG-LEDGER |
| **ArabPixel** and `ps4-linux/ps4-linux-loader` contributors | the kexec payload whose file contract Orbis implements (`vram.txt`, `bootargs.txt`, per-file lookup order) and whose firmware extraction supplies console firmware at boot | `platform/orbis/bootfs.py`, `packaging/loader/` (recipe only; no code copied) |
| **X-F1REBALL-X** — `PS4-Linux` release v1.0 | the verified community initramfs Orbis takes firmware from, the prebuilt 1024 MB payload, and the reference Belize 7.1.7 kernel (all fetched by hash, never redistributed) | `manifests/references.json` |
| **feeRnt** — `ps4-linux-initramfs` | an independent copy of the Marvell firmware that corroborates the community hashes | `manifests/firmware.json` (`observed_in`) |
| **GattoDev (Aryan Hedari)**, **rmuxnet (Armandas Kvietkus)**, **GayCorp** — `initramfs-ps4`; **Piotr Karbowski** — better-initramfs | the boot initramfs source Orbis builds from | `packaging/initramfs/` |
| **rmuxnet** — "Strawberry" kernels | the tutorial's recommended 7.1.7 kernel; candidate for a later Orbis target | `kernel/targets/candidates.md` |
| **Carlos Georges** — "GPU inference on a PS4" | the only public PS4 Vulkan inference record, including the unresolved long-prefill fault | GRAPHICS-REPORT §5 |
| fail0verflow, the original PS4 Linux port authors | the PS4 sub-architecture (`CONFIG_X86_PS4`, Aeolia drivers) that all of the above build on | — |
| Mesa, Linux, llama.cpp (ggml-org), systemd, Arch Linux | the platform | — |

## Licence inventory

| Component | Licence | Shipped by Orbis as | Notes |
|---|---|---|---|
| Orbis platform code, tools, scripts, UI, docs | MIT (`LICENSE`) | source + packages | Nick may choose another licence before the first public release |
| Kernel source + `kernel/patches/*` | GPL-2.0-only | source; binary packages with complete corresponding source | patches carry headers |
| Mesa + `graphics/patches/mesa/*` | MIT (per-file notices) + `LICENSE.ps4-mesa` | source; binary packages | provenance json per patch |
| xf86-video-amdgpu + cursor patch | MIT; **patch licence unstated** | source; binary package | confirm with DionKill/centi_07 before release |
| llama.cpp | MIT | binary package | pinned b11845 |
| initramfs-ps4 + `packaging/initramfs/patches/*` | GPL-3.0; better-initramfs parts BSD-3-Clause | source in `orbis-platform`; locally built archive | Orbis patches are GPL-3.0 like the files they change; the stage-0 overlay is MIT |
| Liverpool/Gladius GPU firmware | **unknown / not granted** | **never in source or redistributable archives** | from a verified community initramfs per profile, hash-checked; images that carry it are marked private (`redistributable: false`) |
| MediaTek MT7668 `mt7668pr2h.bin` | linux-firmware LICENCE.mediatek | **not shipped** (Arch `linux-firmware-mediatek`) | |
| MediaTek MT7668 Wi-Fi blobs (EEPROM, patches, RAM code) | **none recorded** | **never shipped** | operator-supplied |
| Marvell sd8797/sd8897 | linux-firmware LICENCE.Marvell (binary redistribution with Marvell silicon) | rootfs: via Arch `linux-firmware-marvell`; initramfs copy: from the community file, treated as operator-supplied | identity with linux-firmware checked by `firmware-provenance.json` (LIC-003) |
| Fonts/icons | system packages only (no bundled fonts or icons in the UI) | — | UI uses system fonts and an original SVG mark |
| Arch Linux packages | various (per package) | via the Arch Linux Archive snapshot | SBOM lists the snapshot; `packages.txt` lists exact versions |

## Trademarks

"PlayStation", "PS4" and "DualShock" identify the target hardware only. NickLabs PS4 Orbis Linux is an independent open-source project by Nick. It is not affiliated with Sony Interactive Entertainment, and NickLabs is not an incorporated company.
