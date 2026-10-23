# Kernel candidates considered

| Candidate | Southbridges | Source reachable/pinned | Firmware embedded | Orbis use |
|---|---|---|---|---|
| feeRnt `v6.15.4_crashnt-4.7__no-built-in-fw` (commit `108ef976`) | Aeolia, Belize | yes (GitHub) | **yes, MediaTek MT7668** despite the tag name (only Marvell was removed in `615e1a4`) | **v1 primary**, rebuilt with `CONFIG_EXTRA_FIRMWARE=""` |
| rmuxnet "Strawberry" 7.1.7 (tutorial's recommendation) | Aeolia, Belize (+Baikal server) | GitLab — not reachable from the build environment used; config copy inspected via linux-psx `rmux-reference/7.1.7-zaebiz` | yes (Marvell + MediaTek) | candidate; needs a pinned source fetch and a firmware-free rebuild |
| Linux psX `7.2-ps4-baikal-native-dev` | Baikal | yes (GitHub) | no | Baikal target |
| feeRnt `v5.4.247__neocine-1.1` | Baikal | yes | yes | rejected: Mesa 3D does not work on 5.4 (tutorial) |
