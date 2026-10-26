# Community references and loader contract — checked 2026-10-26 on the Orbis build host

## Downloads (X-F1REBALL-X/PS4-Linux release v1.0) — SHA-256 verified, all match Nick's values
| file | bytes | sha256 |
|---|---|---|
| initramfs.cpio.gz | 4102011 | 3f373bd6c469e490eaf4e5cf4d2e8ed77cf7bb91449b6a18da6a6529d479169b |
| linux-1024mb.elf | 320936 | 623c3b68592437b612ede22a4fc1a6f1eef6b17c4b16a1270100ece04cb3c98f |
| bzImage-belize-7.1.7 | 20415488 | 072113898347932c34888cfdc7556953ee27233c0727cc45f419a57119242480 |

## initramfs.cpio.gz contents (48 entries)
- 10 Liverpool files: byte-identical to manifests/firmware.json (recorded earlier from meerzulee/omarchy-psx).
- mrvl/sd8897_uapsta.bin b04fee35…0cc4a (609492 B) and mrvl/sd8797_uapsta.bin c5b84ce8…ad66 (452764 B):
  identical to feeRnt/ps4-linux-initramfs@8f3a62c (2026-01-29). Not compared with kernel.org
  linux-firmware (unreachable from this host).
- No lib/firmware/edid/.
- Scripts: GayCorp initramfs-ps4 lineage (VERSION v0.9.1; functions.sh == GayCorp c998cf0) with
  `init` hard-wired to `mount LABEL=psxitarch /newroot`. Orbis uses only its firmware.

## Reference kernel bzImage-belize-7.1.7
Orbis loader check ok: protocol 2.15, relocatable, zstd payload, CONFIG_X86_PS4=y,
"7.1.7-Strawberry-General-FullLTO+". Embeds Marvell and MT7668 firmware (CONFIG_EXTRA_FIRMWARE).

## ps4-linux-loader@d0f4d4c (pinned; HEAD on 2026-10-26, "Add PS4 14.00 support")
- README lists FW 13.50; Makefile FIRMWARES includes 1350.
- linux/main-aio.c: each of bzImage, initramfs.cpio.gz, bootargs.txt, vram.txt is looked up
  separately in /mnt/usb0, /mnt/usb1, /data/linux/boot/, /user/system/boot/ (first hit wins);
  bootargs.txt: first line only; vram.txt: 32..4609 MB, default 1024.
- ps4-kexec-common/firmware.c + kexec.c: at kexec time the loader builds a cpio with
  key/eap_hdd_key.bin, lib/firmware/edid/my_edid.bin (the TV's EDID) and 8 Liverpool/Gladius
  microcode files (pfp, me, ce, mec, mec2, rlc, sdma, sdma1) extracted from the running console,
  and places it BEFORE the operator's initramfs. Same-path files in the operator initramfs replace
  them. UVD/VCE are not extracted.
- The prebuilt payload's strings contain the same firmware_extract messages.
- Building the payload from source was not done here (the session's permission policy blocked
  running the external project's build); packaging/loader/build-loader-payload.sh is the recipe.

## Orbis initramfs builds from these inputs
See initramfs-cuh-1215a.manifest.json / initramfs-firmware-free.manifest.json (deterministic;
the same bytes from a fresh clone of GayCorp/initramfs-ps4 at c21a720 and from a .git-less
copy with .orbis-commit). Root-selection self-check output is in the manifest.
