# Graphics Report

Scope: the kernel display path, Mesa (radeonsi/RADV), Vulkan compute, and the PS4 GPU-inference fault. Patch-by-patch provenance is in [GRAPHICS-PATCH-AUDIT.md](GRAPHICS-PATCH-AUDIT.md).

**Nothing in this report was observed on a PS4 by Orbis.** Facts come from upstream sources or from host-side tests, and each one says which.

## 1. Stack

| Layer | Orbis choice | Pin |
|---|---|---|
| Kernel display / amdgpu + `ps4_bridge` | feeRnt crashnt-4.7, plus Orbis patch 0001 (1080p60 only) | `108ef97` |
| Firmware (Liverpool CP/ME/MEC/PFP/RLC/SDMA) | operator-supplied in the initramfs, hash-checked | `manifests/firmware.json` |
| Mesa radeonsi (GL) + RADV (Vulkan) | 26.2.5 + linux-psx 0001/0003/0005 | `ddaf85e3`, archive sha256 `794b3d…a0a5` |
| 32-bit Mesa | same source, patches and version (`lib32-mesa-orbis-ps4`) | same |
| X driver | xf86-video-amdgpu 25.0.0 + 64 px cursor | tag |
| libdrm | Arch stock (snapshot) | — |
| Video decode/encode | software only (`-D video-codecs=`, VA off) | — |

## 2. Capability tiers and how each is measured

Implemented in `tools/gpu-probe/orbis-gpu-probe.c` (C, dlopen()s EGL/Vulkan, bounded waits) and `platform/orbis/gpu.py`:

| Tier | Passes only when | Never inferred from |
|---|---|---|
| `gpu.scanout` | DRM driver is `amdgpu` **and** a connector is connected | the amdgpu module being loaded |
| `gpu.gl.hw` | `GL_RENDERER` is an AMD hardware renderer **and** a 64×64 draw lands exactly the expected red/blue pixels | the renderer name alone |
| `gpu.vk.device` | a non-CPU Vulkan physical device with AMD vendor ID | Vulkan instance creation |
| `gpu.vk.compute` | 15 tests pass on that **hardware** device: fill-buffer at 4092/4096/4100/64K/1M/16M with guard words; 1 MiB/64 MiB copy round-trips; dispatch geometry 1×1, 1024×64 and 65535×1 workgroups (every index checked); exact uint32 SAXPY on 1M and 16M elements; shared-memory reduction with atomics; 200 repeated submissions | a pass on a software device. That pass is reported as `UNVERIFIED`: "says nothing about the PS4 GPU" |
| `gpu.vk.inference` / `longrun` | an inference-harness gate receipt for the exact kernel + Mesa + llama.cpp + model, containing evidence that the candidate ran on a hardware AMD device | a smoke test |
| `gpu.video.decode` | — | — (unsupported in v1) |

**Host validation (no PS4):**
- On the build host, the probe classified `llvmpipe (LLVM 20.1.2)` as **software** (`gpu.gl.hw = FAILED`). Its draw test passed (2048 red + 2048 blue pixels, 0 wrong).
- On SwiftShader (CPU Vulkan), all 15 compute tests passed and the tier correctly stayed `UNVERIFIED`.

This is the "silent llvmpipe" defect detector working as intended.

## 3. Display defects (brief items 1–3)

| Defect | Root cause / evidence | Orbis handling | Status |
|---|---|---|---|
| Black screen on first modeset | aday1: `ps4_bridge_disable` asserts VMUTE; the un-mute is the last write of a ~60-command ICC transaction with 650 ms of in-queue delay; an abandoned transaction leaves video muted with sync present. Fixed in maintained trees, and verified on CUH-12xx with 6.15.4-crashnt-4.7. | Use that tree. Pre-install `bzImage` check (protocol, XLF_KERNEL_64, `CONFIG_X86_PS4`). | fix present in source; **untested by Orbis** |
| 120 Hz modeline chosen on a 60 Hz panel | `ps4_bridge_get_modes` also adds VIC 63 | kernel patch 0001; Xorg `PreferredMode 1920x1080` with the exact CEA VIC 16 modeline (`platform/xorg/`) | compile result in RELEASE-STATUS; untested on HW |
| Non-CEA timings rejected | `ps4_bridge_mode_valid` accepts only VIC 16 and 4 | exact 148.5 MHz modeline in Xorg conf | — |
| Hotplug / EDID | EDID is 0 bytes; modes are hard-coded | AI mode is headless and needs no display; `gpu.scanout` is informational in AI | — |
| Recovery without a display | — | SSH + LAN dashboard in every mode. `bootargs.txt` holds `orbis.mode=`, so a PC can set `orbis.mode=ai` on the FAT partition to boot headless | implemented |

**Unverified hypothesis DISP-004:** `pre_enable` writes `HDCPEN_ENC_DIS` (0x05) into `TSRST`, where 0x05 decodes as AVCSRST|FIFOSRST. Left unchanged until a console can A/B test it.

## 4. Mesa / RADV findings relevant to Liverpool (GFX7, "CIK")

These were read from Mesa 26.2.5 source, not observed on hardware.

1. **Liverpool counts as an APU.** The amdgpu PCI table flags `9920/9922/9923/9924` as `AMD_IS_APU`. RADV therefore sets `has_dedicated_vram = false` and reports `VK_PHYSICAL_DEVICE_TYPE_INTEGRATED_GPU`.
2. **The device-local heap is mostly system RAM.** On APUs, RADV reports **2/3 of (GTT + visible VRAM)** as the device-local heap and 1/3 as GTT (`radv_physical_device_init_mem_types`). So Vulkan "VRAM" on a PS4 is largely GTT-backed system memory. Consequences:
   - (a) a bigger `vram.txt` carve-out does not proportionally raise Vulkan capacity; it mainly takes RAM away from Linux;
   - (b) model weights offloaded to Vulkan compete with Linux for the same pages.

   The AI profile therefore defaults to a 128 MB carve-out for CPU inference and 1024 MB (the loader default, the most-tested value) for Vulkan, and lets the harness measure the rest.
3. **Fp16 compute is unavailable.** GFX7 has no packed-math fp16, so `shaderFloat16 = false` (`has_packed_math_16bit = gfx_level >= GFX9`). `storageBuffer16BitAccess` is still `true`. llama.cpp's Vulkan backend therefore runs its **fp16-storage / fp32-arithmetic shader variants**, plus no integer-dot or cooperative-matrix paths. These are the least-exercised variants upstream. That is a plausible contributor to the numerical differences Carlos Georges observed, and stays a hypothesis.
4. **llama.cpp hard-requires 16-bit storage.** The Orbis harness preflight caught this on SwiftShader: `ggml_vulkan: device Vulkan0 does not support 16-bit storage` → `UNSUPPORTED_DEVICE`. RADV/Liverpool advertises it, so this is not a PS4 blocker. It does mean the CPU Vulkan devices available on build hosts cannot stand in for the GPU.
5. **FLAT vs MUBUF.** On GFX7, ACO falls back to FLAT (VGPR) addressing when it cannot prove a 32-bit offset bound. linux-psx tied a Liverpool hang to the FLAT compute-fill path (fixed by 0005, imported). The harness's `shader_scan` step dumps RADV/ACO ISA for llama.cpp's pipelines (`RADV_DEBUG=shaders`) and counts `flat_*` against `buffer_*` instructions, to test whether inference shaders use the same addressing family.
6. **`RADV_DEBUG=nocompute` is not a documented option.** Current Mesa docs list `RADV_QUEUE_DISABLE=compute`. Orbis sets **no** global RADV/AMD debug variables. The probe can test the graphics+compute family and a compute-only family separately (`--queue-family N`).

## 5. The PS4 GPU-inference fault (Carlos Georges, "GPU inference on a PS4")

**What is known (from the public note):**
- all 25 model layers were offloaded to Vulkan, and two short prompts produced exactly the expected output;
- a larger prompt caused a GPU fault **during prefill**, and recovery did not restore the machine;
- there were earlier separate problems in compute, result transfer and model operations, plus arithmetic differences that changed top-token ranking. These were addressed for the short cases by narrower dispatches, a simpler attention path and matching rounding.

The note does not publish the engine version, model, quantization, kernel, Mesa version or fault text.

**Root cause: unknown.** Orbis treats the following as hypotheses, each with a discriminating experiment in the harness:

| # | Hypothesis | Discriminating experiment | Harness feature |
|---|---|---|---|
| H1 | Shaders on GFX7 use FLAT addressing for large offsets/buffers (same family as linux-psx 0005) | `flat_*` count per pipeline; does the failing ctx coincide with buffers > 4 GiB/2³² offsets? | `shader_scan`, VM fault address vs buffer ranges via `RADV_DEBUG=hang` dumps |
| H2 | Dispatch geometry: prefill with large `n_ubatch` creates grids or workgroup counts the hardware or driver mishandles | failure tracks **ubatch** at fixed ctx → H2/H3; tracks **ctx** at fixed ubatch → attention/KV path | `axes.ubatch` × `axes.ctx` sweep with boundary bisection |
| H3 | Memory: compute buffers (∝ ubatch × n_vocab) plus KV exhaust GTT/TTM, so eviction or allocation races fault | record GTT/VRAM usage and `MemAvailable` before/after each step; repeat at smaller `vram.txt` | per-step `mem_info_*` samples; OOM classification |
| H4 | Synchronization race (barriers/queues) | fault disappears with `RADV_DEBUG=syncshaders` or `hang` (which implies syncshaders) → race | candidate `env` per campaign; Mesa docs warn `hang` can mask races |
| H5 | Numerical: fp32-arithmetic variant differs from the CPU reference before any fault | intermediate tensors diverge before the failing ctx | `tensors` step (llama-eval-callback sums, first divergent op) |
| H6 | Kernel: GPU reset/recovery on PS4 cannot restore rings, so any fault is fatal | after a fault, does the probe pass without reboot? | not attempted automatically; `amdgpu.gpu_recovery=0` + UMR workflow documented below |

**Harness (`tools/inference-harness/orbis_infer_harness.py`):**
- Pins model sha256, llama.cpp build, kernel, cmdline, Mesa (from the probe) and RADV env. A pin change aborts a resumed campaign.
- CPU reference logits (`llama-perplexity --kl-divergence-base`) against the Vulkan candidate: mean/p99/max KLD and top-1 agreement.
- Intermediate tensors via `llama-eval-callback`: reports the first diverging op.
- `llama-server` greedy completions on mixed prompt lengths in one process (state reuse).
- Every step is fsync-journalled as *started* before the GPU is touched. After a hard hang and power cycle, `resume` marks that step `HANG_OR_RESET_INFERRED`. Kernel messages stream live to a file and optionally over UDP to another machine (`receive` subcommand). Use with `netconsole=` for messages during a hang.
- Outcomes: `PASS`, `NUMERIC_MISMATCH`, `OUT_OF_MEMORY`, `GPU_PAGE_FAULT`, `RING_TIMEOUT`, `GPU_RESET`, `DEVICE_LOST`, `UNSUPPORTED_DEVICE`, `CRASH`, `TIMEOUT`, `TOOL_ERROR`, `HANG_OR_RESET_INFERRED`, `SETUP_ERROR`.
- Boundary bisection: ascending ctx, then bisect between last pass and first failure. Bisection is skipped after a hang unless `bisect_hangs` is set, because each probe could cost a power cycle.
- Gate: short prompts + long prefill + mixed-server identical + tensors pass, **and** evidence that the candidate ran on a hardware AMD device. The receipt (`/var/lib/orbis/gpu-gate.json`) is the only thing that admits Vulkan in AI mode, and only for the exact pins.

**Host self-tests:** fault-injecting stand-ins under `tests/fixtures/fake-llama-gpu`, not a GPU. Each injected failure was found and classified correctly:

| Injected failure | Harness result |
|---|---|
| none | 7/7 PASS; gate correctly **refused**: no hardware AMD GPU evidence |
| page fault at ctx ≥ 448 | boundary found at **(384 pass, 448 fail)**, outcome `GPU_PAGE_FAULT` from kmsg |
| wrong weights at ctx 256 | `NUMERIC_MISMATCH` (mean KLD 8.28, same-top 0%); boundary (192, 256) |
| hang at ctx ≥ 512 | `TIMEOUT`; the server path also `TIMEOUT` |
| power loss mid-step | `resume` marked it `HANG_OR_RESET_INFERRED` |

One genuine tool anomaly turned up: llama-perplexity sometimes exits 0 without its statistics. That is classified `TOOL_ERROR` after retries, never as a GPU verdict (BUG-LEDGER TOOL-001).

**Debug workflow on a development build only** (Mesa hang-debugging docs):
1. Install UMR. `chmod +s $(which umr)`; make `/sys/kernel/debug/dri` readable.
2. Run the failing step with `RADV_DEBUG=hang`. Inspect `~/radv_dumps_*/trace.log`, `vm_fault.log`, `bo_ranges.log`, `umr_waves.log`.
3. If the bug disappears (a race masked by syncshaders), boot with `amdgpu.gpu_recovery=0` and dump waves/rings with `umr --by-pci <id> -O bits,halt_waves -go 0 -wa <ring> -go 1` and `umr -RS <ring>`.

Production images never ship these settings.

**Fix status: pending.** No fix is claimed. Patches that come out of this investigation go into `graphics/patches/mesa/` with a provenance entry and a harness campaign as evidence.

## 6. Other brief items

| Item | Status |
|---|---|
| (4) software rendering reported as GPU | detected (`gpu.gl.hw` FAILED + dashboard alert); validated on llvmpipe |
| (5) game artefacts / Vulkan crashes | Vulkan graphics not advertised; per-title compat list (`manifests/gaming-compat.json`) starts **empty**. Titles are added only with a recorded test. |
| (6) 32-bit alignment | lib32 package from the same source/version; `orbis doctor` warns on mismatch |
| (7) driver regressions on update | pacman guard hooks block generic Mesa/kernels and community `*-ps4` packages (unit-tested) |
| (8) GPU reset | not claimed. After any fault the engine requires a clean reboot before a GPU-using mode starts (`graphics_state_clean`) |
| (9) video codecs | software decode; UVD/VCE `UNSUPPORTED` in v1 |
