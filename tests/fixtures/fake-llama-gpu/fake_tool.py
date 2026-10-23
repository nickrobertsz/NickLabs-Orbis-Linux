#!/usr/bin/env python3
"""Fault-injecting stand-in for a GPU llama.cpp build (harness self-test only).

Delegates to the real CPU binaries in $ORBIS_FAKE_REAL_BIN, but:
  * ctx >= $ORBIS_FAKE_FAIL_CTX: appends an amdgpu page-fault line to
    $ORBIS_HARNESS_KMSG_SOURCE and exits like a lost Vulkan device;
  * ctx in $ORBIS_FAKE_NUMERIC_CTX (comma list): runs against
    $ORBIS_FAKE_WRONG_MODEL so logits differ (numeric mismatch);
  * ctx >= $ORBIS_FAKE_HANG_CTX: sleeps (hang/timeout path).
Never shipped; lives under tests/fixtures.
"""
import os
import sys
import time

tool = os.path.basename(sys.argv[0])
args = sys.argv[1:]
ctx = None
if "-c" in args:
    ctx = int(args[args.index("-c") + 1])
real = os.path.join(os.environ["ORBIS_FAKE_REAL_BIN"], tool)
# The CPU build has no GPU: drop GPU-only flags.
clean = []
skip = False
for i, a in enumerate(args):
    if skip:
        skip = False
        continue
    if a in ("-ngl", "--n-gpu-layers", "--device", "-dev"):
        skip = True
        continue
    clean.append(a)
fail = int(os.environ.get("ORBIS_FAKE_FAIL_CTX", "0") or 0)
hang = int(os.environ.get("ORBIS_FAKE_HANG_CTX", "0") or 0)
numeric = {int(x) for x in os.environ.get("ORBIS_FAKE_NUMERIC_CTX", "").split(",") if x}
if ctx is not None and hang and ctx >= hang:
    time.sleep(3600)
if ctx is not None and fail and ctx >= fail:
    with open(os.environ["ORBIS_HARNESS_KMSG_SOURCE"], "a") as fh:
        fh.write("amdgpu 0000:00:01.0: amdgpu: [gfxhub] page fault (src_id:0 ring:24 vmid:3 pasid:32769)\n")
        fh.write("amdgpu 0000:00:01.0: amdgpu:   in page starting at address 0x0000800102400000 from client 0x1b (UTCL2)\n")
    time.sleep(0.5)
    print("ggml_vulkan: vk::Queue::submit: ErrorDeviceLost", file=sys.stderr)
    sys.exit(134)
if ctx in numeric:
    i = clean.index("-m")
    clean[i + 1] = os.environ["ORBIS_FAKE_WRONG_MODEL"]
os.execv(real, [real] + clean)
