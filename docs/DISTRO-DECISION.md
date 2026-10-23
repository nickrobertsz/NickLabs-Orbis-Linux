# Distro Decision Record — NickLabs PS4 Orbis Linux v1

**Date:** 2026-10-23  **Status:** decided for v1, revisit at v2  **Owner:** Nick (NickLabs)

## Decision

**Orbis v1 uses plain Arch Linux, from its generic `x86_64` repositories, pinned to one Arch Linux Archive snapshot date (`2026/10/20` candidate).** On top of it sit three Orbis-built PS4 packages (kernel, Mesa, X driver) and the distro-independent Orbis platform layer.

Fedora Atomic/bootc was the stated preference "conditional on feasibility". It is **not qualified** today. It remains a tracked v2 candidate with a concrete qualification plan (below). The boot adapter Orbis needs for Arch is the same one bootc would need, so that work carries over.

## What was inspected (evidence, not assumptions)

| Source | Revision | What it established |
|---|---|---|
| `ps4-linux/ps4-linux-loader` | `d0f4d4c` | The PS4 payload has **no bootloader**. It loads `bzImage`, `initramfs.cpio.gz`, `bootargs.txt` (first line only) and `vram.txt` from the USB FAT root (then the internal `/data/linux/boot`). `vram.txt` is a **boot-time carve-out** written into the E820 map and the GPU memory controller. Valid range 32–4609 MB, default 1024; the MTRR mask assumes a power of two. |
| `GayCorp/initramfs-ps4` | `c21a720` | The community initramfs (better-initramfs based) supports `root=`, `rootflags=`, `init=`. It has no ostree/composefs support. Its CI pulls the Liverpool GPU firmware from a **private** repository. |
| `DionKill/ps4-video-archlinux` | `57e0f8e` | The PS4 Mesa/libdrm/X driver packages exist **only for Arch**. The Mesa PKGBUILD builds from a **moving `git` HEAD** with `sha256sums=SKIP`. The published binary is `26.0.8 (60e95b78785)` while the PKGBUILD says `26.2.2`. The packages are unsigned (`SigLevel = Optional`). The libdrm patch only adds marketing names to `amdgpu.ids`. |
| `meerzulee/linux-psx` + `omarchy-psx` | `2138063` / `625cb1b` | This is the most rigorous current work: a maintained Baikal kernel, a pinned Mesa 26.2.5 patch series with provenance, and recorded hardware experiments. It too is Arch-based (Omarchy) and pins an **Arch snapshot date**. |
| `DionKill/ps4-linux-tutorial` | `82b5249` | Community guidance is "Arch-based distros are recommended … the only ones that currently have automatic updates without breaking". CachyOS Light ships as a pre-installed tarball on Mega/Mediafire. |
| `feeRnt/ps4-linux-12xx` | tag `v6.15.4_crashnt-4.7__no-built-in-fw` → `108ef97` | This is the 6.15.4 kernel line confirmed to fix the HDMI black screen on a CUH-12xx. Its build uses `-march=btver2` (Jaguar). Despite its tag name it **still embeds MediaTek MT7668 firmware**: only the Marvell blob was removed, in `615e1a4`. |
| `bootc-dev/bootc#2375` | issue page | Non-EFI systems that use BLS entries fail `bootc status/upgrade/switch` because `get_bootloader()` assumes GRUB when no EFI variables exist. A fix is proposed; I could not confirm it is merged or released. |
| Kernel configs (feeRnt 6.15.4, rmux 7.1.7 reference, linux-psx 7.2) | — | `CONFIG_EROFS_FS` unset (composefs impossible). `CONFIG_PSI` unset. `CONFIG_CGROUP_BPF` unset. `CONFIG_SUSPEND`/`HIBERNATION` unset. `CONFIG_HSA_AMD` unset (no ROCm). `CONFIG_BTRFS_FS=y` and `CONFIG_MEMCG=y` are present. |
| Fedora x86-64-v3 change | Phoronix, 2026-08-18 | FESCo rejected v3 packages for F45. The proposal was additive in any case, so Fedora's x86_64 baseline stays v1, which is Jaguar-safe. |
| Bazzite install guide | docs.bazzite.gg | Its requirements are UEFI ("CSM/Legacy boot UNSUPPORTED") and "Vulkan 1.3+". |

No distribution ISOs were downloaded. Only sources, manifests, configs and one reference kernel binary (to test the boot-protocol checker) were fetched.

## Candidates scored

Scores run 0–3, higher is better. Facts come from the table above. Rows marked *(est.)* are reasoned estimates, not measurements.

| Criterion | **Arch generic + Orbis pins** | Fedora bootc (lean) | CachyOS Light tarball | Nobara | Bazzite | Debian / Buildroot |
|---|---|---|---|---|---|---|
| 1. Verified PS4 GPU/peripheral compatibility | **3** (all PS4 Mesa work and community testing is Arch) | 0 (no Fedora PS4 Mesa RPM; no PS4 boots recorded) | 2 (works for users, but unpinned) | 1 (historical ports only) | 0 | 1 (Buildroot booted in one research project; no desktop stack) |
| 2. Idle RAM / predictability *(est.)* | 3 (we choose every package) | 2 (bootc base image carries more than needed) | 2 (pre-riced desktop) | 1 | 0 (gaming stack always present) | 3 |
| 3. Graphics + game compatibility | 3 (patched Mesa + Steam in multilib) | 1 (would need everything rebuilt) | 3 | 2 | 1 | 1 |
| 4. Provenance / regression-free updates | 2 → **3 with Orbis pins** (archive snapshot + guarded packages) | 3 (image-based) | 0 (binary tarball, git-HEAD Mesa) | 1 | 2 | 2 |
| 5. Atomicity / rollback / reproducibility | 2 (archive snapshot; kernel A/B pair; btrfs snapshots when `/` is btrfs) | **3 in principle**, 0 on PS4 today (blockers B1–B5) | 0 | 1 | 3 in principle, 0 on PS4 | 2 |
| 6. CPU ISA safety | 3 (generic `x86_64`; Orbis audits `pacman.conf` and binaries) | 3 (Fedora stays v1) | 1 (CachyOS v3/v4 repos would SIGILL; only safe with generic repos) | 3 | 3 | 3 |
| 7. Build complexity / maintenance / licensing | 2 | 1 | 0 | 1 | 1 | 0 (we would own every PS4 graphics patch) |
| **Total** | **19–20** | 13 (8 on PS4 today) | 8 | 10 | 10 | 12 |

## Why Fedora bootc is not qualified yet: blockers

| # | Blocker | Evidence | What would solve it |
|---|---|---|---|
| B1 | No bootloader exists for bootc/ostree to drive. The PS4 payload loads fixed file names from FAT. | `ps4-linux-loader/linux/main-aio.c` | An adapter that materialises the default BLS entry into the loader's files and keeps the previous pair. **Orbis implements this pattern already** (`platform/orbis/bootfs.py`: kernel pair install, `.orbis-prev`, loader-contract check). A post-finalize hook (`ostree-finalize-staged` → `orbis boot sync`) is the missing glue. |
| B2 | ostree needs `ostree-prepare-root` in the initramfs; the community initramfs has none. | `initramfs-ps4/init`, `functions.sh` | A dracut initramfs with PS4 firmware (operator-supplied) and the PS4 kernel's modules. |
| B3 | composefs (the default bootc backend) needs EROFS; PS4 kernels disable it. | `# CONFIG_EROFS_FS is not set` in all three configs | Add `CONFIG_EROFS_FS=y` (prepared as a commented candidate in `kernel/config/orbis-aeolia-belize.fragment`) or disable composefs in `prepare-root.conf`. |
| B4 | bootc's non-EFI detection fails on BLS-without-EFI systems. | bootc#2375 (fix proposed; merge/release unconfirmed) | Use a bootc release that contains the fix, then verify `bootc status` and rollback accounting on the console itself. |
| B5 | No Fedora build of the PS4 Mesa patches exists, and Fedora has zero recorded PS4 boots. | — | An RPM spec mirroring `graphics/mesa-orbis-ps4/PKGBUILD`, then hardware tests. |

**Fedora qualification plan (v2):** build a `fedora-bootc` image containing `linux-orbis-ps4` as an RPM, `mesa-orbis-ps4` as an RPM, `orbis-platform` and a dracut initramfs. Boot it with the B1 adapter. It is qualified only when all of these pass on a physical console: `bootc status` matches the booted deployment; `bootc upgrade` followed by a reboot lands on the new deployment; `bootc rollback` followed by a reboot lands on the previous one; and the GPU tiers stay identical.

## Rejected / reference-only

- **CachyOS Light:** not reproducible. It is a binary root tarball with a git-HEAD Mesa. Its v3/v4 repositories are unsafe on Jaguar. Orbis's overlay installer (`packaging/scripts/orbis-install-overlay.sh`) can still convert an existing CachyOS Light install to the Orbis stack, as long as only generic repos are configured.
- **Nobara:** no current PS4 port. Its value is gaming configuration knowledge; nothing is imported.
- **Bazzite:** requires UEFI and Vulkan 1.3+. It is a reference for gaming UX and image-based updates only.
- **Debian/Alpine/Buildroot:** Buildroot is excellent for a GPU bring-up image (Carlos Georges used it). As a daily driver it would mean owning every PS4 graphics patch ourselves.

## Consequences

- Updates are **controlled**. Arch packages move only to an Arch Linux Archive date listed in `/usr/share/orbis/qualified-snapshots` (`orbis-update`). Orbis's PS4 packages come only from the Orbis repository.
- Generic Mesa, generic kernels and the unpinned community `*-ps4` packages are **blocked** by pacman hooks (`platform/pacman-hooks/`). A deliberate change needs a one-shot, logged override.
- **Rollback in v1 is:**
  - the previous kernel+initramfs pair on the boot partition (`orbis boot rollback-kernel`), restorable even from another PC over FTP by renaming `*.orbis-prev`;
  - package-list checkpoints;
  - read-only btrfs snapshots when the root is btrfs.

  It is not whole-image atomic like bootc. That gap is the main reason bootc stays on the roadmap.
