"""AI planner, capability matrix, GPU classification, ISA audit, package guard, inventory."""

import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from helpers import REPO, FixtureRoot, make_gguf

from orbis import ai, capabilities, config, gpu, hardware, isa_audit, pkgguard


def tiny_model(**over) -> ai.ModelInfo:
    kv = {"general.architecture": "llama", "general.name": "test",
          "llama.block_count": 28, "llama.context_length": 32768, "llama.embedding_length": 1536,
          "llama.attention.head_count": 12, "llama.attention.head_count_kv": 2,
          "tokenizer.ggml.tokens": ["a", "b", "c"]}
    kv.update(over)
    p = make_gguf(Path(tempfile.mkstemp(suffix=".gguf")[1]), kv)
    with open(p, "ab") as fh:              # pretend ~900 MiB of weights
        fh.truncate(900 << 20)
    return ai.ModelInfo.from_file(p)


class AiTests(unittest.TestCase):
    def test_gguf_metadata_and_kv_cache(self):
        m = tiny_model()
        self.assertEqual((m.arch, m.n_layer, m.n_head_kv, m.head_dim_k), ("llama", 28, 2, 128))
        self.assertEqual(m.n_vocab, 3)
        # 28 layers * 4096 ctx * 2 heads * (128+128) dims * 2 bytes
        self.assertEqual(m.kv_cache_bytes(4096, "f16"), 28 * 4096 * 2 * 256 * 2)

    def test_context_capped_by_measured_memory(self):
        m = tiny_model()
        cfg = {"ai": {"ctx_size": 8192, "headroom_mib": 512}}
        lp = ai.plan_launch(cfg, m, mem_available_kib=1500 * 1024, ncpu=8, vk_heap_bytes=None,
                            gate={}, ident={})
        self.assertLess(lp.n_ctx, 8192)
        self.assertFalse(lp.errors)
        self.assertEqual(lp.active_backend, "cpu")
        self.assertIn("--device", lp.argv)

    def test_too_large_model_reports_error(self):
        m = tiny_model()
        lp = ai.plan_launch({"ai": {}}, m, mem_available_kib=600 * 1024, ncpu=8, vk_heap_bytes=None,
                            gate={}, ident={})
        self.assertTrue(lp.errors)

    def test_vulkan_refused_without_gate_and_says_so(self):
        m = tiny_model()
        lp = ai.plan_launch({"ai": {"backend": "vulkan"}}, m, 5_000_000, 8, 3 << 30, gate={}, ident={})
        self.assertEqual(lp.active_backend, "cpu")
        self.assertIn("not admitted", lp.backend_reason)
        self.assertEqual(lp.n_gpu_layers, 0)

    def test_vulkan_gate_must_match_stack(self):
        m = tiny_model()
        gate = {"inference_gate": {"passed": True, "max_prompt_tokens": 512},
                "pins": {"kernel_release": "6.15.4-a", "mesa_version": "26.2.5"}}
        lp = ai.plan_launch({"ai": {"backend": "vulkan"}}, m, 5_000_000, 8, 3 << 30, gate,
                            {"kernel_release": "6.15.4-b", "mesa_version": "26.2.5"})
        self.assertEqual(lp.active_backend, "cpu")
        self.assertIn("different kernel_release", lp.backend_reason)
        lp = ai.plan_launch({"ai": {"backend": "vulkan"}}, m, 5_000_000, 8, 3 << 30, gate,
                            {"kernel_release": "6.15.4-a", "mesa_version": "26.2.5"})
        self.assertEqual(lp.active_backend, "vulkan")
        self.assertGreater(lp.n_gpu_layers, 0)
        self.assertLessEqual(lp.n_ubatch, 512)          # capped to the gate's proven prompt size

    @unittest.skipUnless(Path("/home/claude/models/orbis-tiny-f32.gguf").exists(), "tiny model not generated")
    def test_real_gguf_written_by_gguf_py(self):
        m = ai.ModelInfo.from_file("/home/claude/models/orbis-tiny-f32.gguf")
        self.assertEqual((m.arch, m.n_layer, m.n_embd, m.n_head_kv), ("llama", 4, 128, 2))


class CapabilityTests(unittest.TestCase):
    def plat(self, sb, gpu_fam="liverpool"):
        return {"is_ps4": True, "southbridge": sb, "gpu": {"family": gpu_fam},
                "console_group": hardware.console_group(sb, gpu_fam)}

    def test_belize_needs_declared_model(self):
        g, notes = capabilities.group_key(self.plat("belize"), None)
        self.assertIsNone(g)
        self.assertTrue(notes)
        self.assertEqual(capabilities.group_key(self.plat("belize"), "CUH-1215A")[0], "fat-belize")
        self.assertEqual(capabilities.group_key(self.plat("belize"), "CUH-2216A")[0], "slim-belize")

    def test_contradicting_declaration_uses_silicon(self):
        g, notes = capabilities.group_key(self.plat("aeolia"), "CUH-2216A")
        self.assertEqual(g, "fat-aeolia")
        self.assertTrue(any("contradicts" in n for n in notes))

    def test_no_leakage_between_groups(self):
        res = capabilities.evaluate(self.plat("aeolia"), None)
        row = {c["id"]: c for c in res["capabilities"]}
        # Baikal-only evidence must not appear on an Aeolia console.
        self.assertEqual(row["net.ethernet"]["effective"], "UNVERIFIED")
        self.assertEqual(row["audio.hdmi"]["effective"], "UNVERIFIED")
        self.assertEqual(row["display.hdmi_1080p60"]["effective"], "UPSTREAM_REPORTED")

    def test_orbis_policy_overrides_upstream_optimism(self):
        res = capabilities.evaluate(self.plat("aeolia"), None)
        row = {c["id"]: c for c in res["capabilities"]}
        self.assertEqual(row["storage.internal_hdd_root"]["static"]["state"], "UPSTREAM_REPORTED")
        self.assertEqual(row["storage.internal_hdd_root"]["effective"], "UNSUPPORTED")
        self.assertEqual(row["gpu.vk.inference"]["effective"], "BLOCKED")

    def test_nothing_reads_verified_without_receipts(self):
        res = capabilities.evaluate(self.plat("baikal"), None)
        self.assertFalse([c for c in res["capabilities"] if c["effective"] == "VERIFIED"])

    def test_not_ps4(self):
        res = capabilities.evaluate({"is_ps4": False, "southbridge": None, "gpu": None,
                                     "console_group": {}}, None)
        self.assertIsNone(res["group"])
        self.assertTrue(all(c["effective"] in ("UNVERIFIED", "UNSUPPORTED", "BLOCKED") for c in res["capabilities"]))


class GpuTests(unittest.TestCase):
    def test_renderer_classification(self):
        for s, want in (("llvmpipe (LLVM 20.1.2, 256 bits)", "software"),
                        ("AMD Radeon Graphics (radeonsi, liverpool, LLVM 20.1.8, DRM 3.61)", "hardware"),
                        ("zink Vulkan 1.3(llvmpipe (LLVM 19))", "software"),
                        ("softpipe", "software"), ("SwiftShader Device (Subzero)", "software"),
                        ("Some Unknown GPU", "unknown"), (None, "unknown")):
            self.assertEqual(gpu.classify_renderer(s), want, s)

    def test_vk_device_classification(self):
        self.assertEqual(gpu.classify_vk_device({"device_type": 4}), "software")
        self.assertEqual(gpu.classify_vk_device({"device_type": 1, "vendor_id": 0x1002, "driver_id": 3}), "hardware")
        self.assertEqual(gpu.classify_vk_device({"device_type": 1, "vendor_id": 0x10005, "driver_id": 13}), "software")

    def test_summary_flags_software_fallback(self):
        probe = {"gl": {"renderer": "llvmpipe (LLVM 20)"},
                 "vulkan": {"devices": [{"name": "llvmpipe", "device_type": 4, "vendor_id": 0x10005}]},
                 "compute": {"device": {"device_type": 4, "name": "llvmpipe"}, "passed_count": 15,
                             "failed_count": 0, "fatal": False, "tests": []}}
        t = gpu.summarize(probe, {"drm": {"cards": [{"driver": "amdgpu"}], "connectors": []}})
        self.assertEqual(t["gpu.gl.hw"]["state"], "FAILED")
        self.assertEqual(t["gpu.vk.device"]["state"], "FAILED")
        self.assertEqual(t["gpu.vk.compute"]["state"], "UNVERIFIED")   # software pass proves nothing

    def test_summary_hardware_pass_and_fatal(self):
        dev = {"name": "AMD Radeon Graphics (RADV LIVERPOOL)", "device_type": 1, "vendor_id": 0x1002,
               "driver_name": "radv", "driver_info": "Mesa 26.2.5", "driver_id": 3}
        probe = {"gl": {"renderer": "AMD Radeon Graphics (radeonsi, liverpool)"},
                 "vulkan": {"devices": [dev]},
                 "compute": {"device": dev, "passed_count": 3, "failed_count": 1, "fatal": True,
                             "fatal_reason": "VK_TIMEOUT", "tests": [{"test": "fill_16777216", "ok": False}]}}
        t = gpu.summarize(probe, {"drm": {"cards": [{"driver": "amdgpu"}],
                                          "connectors": [{"name": "card0-HDMI-A-1", "status": "connected"}]}})
        self.assertEqual(t["gpu.scanout"]["state"], "PASSED_ON_THIS_DEVICE")
        self.assertEqual(t["gpu.gl.hw"]["state"], "PASSED_ON_THIS_DEVICE")
        self.assertEqual(t["gpu.vk.device"]["state"], "PASSED_ON_THIS_DEVICE")
        self.assertEqual(t["gpu.vk.compute"]["state"], "FAILED")
        self.assertIn("VK_TIMEOUT", t["gpu.vk.compute"]["evidence"])

    def test_fault_events(self):
        lines = ["[ 12.1] amdgpu 0000:00:01.0: amdgpu: [gfxhub] page fault (src_id:0 ring:24 vmid:3)",
                 "[ 22.0] [drm:amdgpu_job_timedout [amdgpu]] *ERROR* ring gfx timeout, signaled seq=1",
                 "[ 23.0] amdgpu 0000:00:01.0: amdgpu: GPU reset begin!",
                 "[  1.0] amdgpu 0000:00:01.0: VRAM: 1024M"]
        ev = gpu.fault_events(lines)
        self.assertEqual(set(ev), {"vm_fault", "ring_timeout", "gpu_reset"})
        clean, _ = gpu.graphics_state_clean(lines[3:])
        self.assertTrue(clean)


class IsaTests(unittest.TestCase):
    def test_instruction_classes(self):
        c = isa_audit.classify_insn
        self.assertEqual(c("shlx", "%eax,%ebx,%ecx"), "bmi2")
        self.assertEqual(c("vfmadd231ps", "%ymm1,%ymm2,%ymm3"), "fma")
        self.assertEqual(c("vpaddd", "%ymm1,%ymm2,%ymm3"), "avx2")       # 256-bit integer
        self.assertIsNone(c("vpaddd", "%xmm1,%xmm2,%xmm3"))               # AVX1 128-bit: fine on Jaguar
        self.assertIsNone(c("vaddps", "%ymm1,%ymm2,%ymm3"))               # AVX1 256-bit float: fine
        self.assertIsNone(c("andn", "%eax,%ebx,%ecx"))                     # BMI1: Jaguar has it
        self.assertEqual(c("vmovdqu64", "%zmm0,(%rax)"), "avx512")

    @unittest.skipUnless(shutil.which("gcc") and shutil.which("objdump"), "needs gcc + objdump")
    def test_detects_v3_build(self):
        d = Path(tempfile.mkdtemp())
        src = d / "t.c"
        src.write_text("unsigned f(unsigned*a,unsigned n,unsigned s){unsigned r=0;for(unsigned i=0;i<n;i++)"
                       "r+=(a[i]<<s)^(a[i]>>(s+1));return r;}\nint main(){return 0;}\n")
        for flags, want in ((["-march=x86-64-v3"], "unsafe"), (["-march=btver2"], "safe")):
            out = d / f"t{len(flags[0])}"
            subprocess.run(["gcc", "-O3", *flags, "-o", str(out), str(src)], check=True)
            self.assertEqual(isa_audit.audit_file(out)["verdict"], want, flags)

    def test_pacman_v3_repo_is_flagged(self):
        with FixtureRoot() as fx:
            (fx.root / "etc/pacman.conf").write_text(
                "[options]\nArchitecture = x86_64 x86_64_v3\n\n[cachyos-v3]\nInclude = x\n[core]\n")
            local = fx.root / "var/lib/pacman/local/zstd-1.5-1"
            local.mkdir(parents=True)
            (local / "desc").write_text("%NAME%\nzstd\n\n%ARCH%\nx86_64_v3\n")
            r = isa_audit.pacman_audit()
            self.assertFalse(r["ok"])
            self.assertEqual(r["bad_repos"], ["cachyos-v3"])
            self.assertEqual(r["foreign_arch_packages"][0]["arch"], "x86_64_v3")

    def test_pacman_generic_is_ok(self):
        with FixtureRoot() as fx:
            (fx.root / "etc/pacman.conf").write_text("[options]\nArchitecture = auto\n[core]\n[extra]\n[orbis]\n")
            self.assertTrue(isa_audit.pacman_audit()["ok"])


class PkgGuardTests(unittest.TestCase):
    def test_blocks_generic_mesa_and_kernels(self):
        ok, bad = pkgguard.evaluate("install", ["firefox", "mesa", "linux-zen", "lib32-vulkan-radeon"])
        self.assertFalse(ok)
        self.assertEqual(bad, ["mesa", "linux-zen", "lib32-vulkan-radeon"])
        self.assertTrue(pkgguard.evaluate("install", ["firefox", "mesa-orbis-ps4"])[0])
        self.assertFalse(pkgguard.evaluate("remove", ["mesa-orbis-ps4"])[0])
        self.assertTrue(pkgguard.evaluate("remove", ["firefox"])[0])

    def test_override_is_single_use(self):
        with FixtureRoot() as fx:
            (fx.root / "etc/orbis/allow-graphics-change").write_text("testing Mesa 26.3 rc\n")
            import sys
            for expect in (0, 1):
                old = sys.stdin
                sys.stdin = io.StringIO("mesa\n")
                try:
                    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                        self.assertEqual(pkgguard.main(["install"]), expect)
                finally:
                    sys.stdin = old
            log = (fx.root / "var/log/orbis/pkg-guard.log").read_text().splitlines()
            self.assertEqual([json.loads(l)["decision"] for l in log], ["allowed-by-override", "blocked"])


class HardwareTests(unittest.TestCase):
    def test_fixture_ps4_inventory(self):
        with FixtureRoot() as fx:
            inv = hardware.inventory()
            p = inv["platform"]
            self.assertTrue(p["is_ps4"])
            self.assertEqual(p["southbridge"], "belize")
            self.assertEqual(p["gpu"]["family"], "liverpool")
            self.assertEqual(p["console_group"]["group"], "fat-or-slim")
            self.assertTrue(inv["cpu"]["jaguar_like"])
            self.assertEqual(inv["cpu"]["isa_level"], "x86-64-v2")
            self.assertEqual(inv["gpu_memory"]["mem_info_vram_total"], 1024 << 20)
            self.assertEqual(inv["sensors"]["temperatures"][0]["celsius"], 58.25)
            self.assertEqual(inv["sensors"]["fan_control"], "UNSUPPORTED")
            self.assertEqual(inv["kernel"]["orbis_mode_arg"], "pc")

    def test_gladius_is_pro(self):
        with FixtureRoot(southbridge_dev=0x90DB, gpu_dev=0x9924):
            p = hardware.detect_platform()
            self.assertEqual((p["southbridge"], p["console_group"]["group"]), ("baikal", "pro"))

    def test_this_host_is_not_a_ps4(self):
        self.assertFalse(hardware.detect_platform(pci=[])["is_ps4"])

    def test_cuh_model_groups(self):
        self.assertEqual(hardware.model_group_from_cuh("CUH-1215A"), "fat")
        self.assertEqual(hardware.model_group_from_cuh("cuh-2216b"), "slim")
        self.assertEqual(hardware.model_group_from_cuh("CUH-7216B"), "pro")
        self.assertIsNone(hardware.model_group_from_cuh("XYZ"))


class ConfigTests(unittest.TestCase):
    def test_profiles_load_and_are_consistent(self):
        with FixtureRoot():
            profs = config.load_profiles()
            self.assertEqual(set(profs), {"pc", "gaming", "ai"})
            self.assertFalse(profs["ai"].display)
            self.assertFalse(profs["ai"].allow_gpu_clients)
            self.assertEqual(profs["ai"].vram_for_backend("vulkan"), (512, 1024))
            self.assertEqual(profs["ai"].vram_for_backend("cpu"), (32, 128))
            for m, p in profs.items():
                self.assertEqual(p.target, f"orbis-{m}.target")
                self.assertTrue(p.owned_patterns)

    def test_settings_validation(self):
        with FixtureRoot():
            config.set_setting("ai.backend", "vulkan")
            self.assertEqual(config.load_config()["ai"]["backend"], "vulkan")
            with self.assertRaises(config.ConfigError):
                config.set_setting("ai.backend", "cuda")
            with self.assertRaises(config.ConfigError):
                config.set_setting("boot.allowed_vram_mb", [4096])


if __name__ == "__main__":
    unittest.main()


class NetPolicyTests(unittest.TestCase):
    def _run(self, rc, out="", err=""):
        class R:
            returncode, stdout, stderr = rc, out, err
        return lambda *a, **k: R()

    def test_states(self):
        from unittest import mock
        from orbis import netpolicy
        table = "table inet orbis_lan_only {\n chain output {\n ip daddr @lan4 accept\n }\n}"
        with mock.patch("shutil.which", return_value="/usr/bin/nft"):
            self.assertEqual(netpolicy.status({}, run=self._run(0, table))["severity"], "OK")
            st = netpolicy.status({}, run=self._run(1, err="Error: No such file or directory"))
            self.assertEqual(st["severity"], "FAIL")
            self.assertFalse(st["active"])
            st = netpolicy.status({}, run=self._run(1, err="Operation not permitted"))
            self.assertEqual((st["severity"], st["active"]), ("INFO", None))
            st = netpolicy.status({"network": {"lan_only": False}}, run=self._run(1, err="No such file"))
            self.assertEqual(st["severity"], "OK")
            self.assertIn("disabled", st["message"])
        with mock.patch("shutil.which", return_value=None):
            self.assertIsNone(netpolicy.status({})["active"])

    def test_ruleset_shape(self):
        text = (REPO / "platform/misc/nftables-orbis-lan-only.nft").read_text()
        for needle in ("table inet orbis_lan_only", "192.168.0.0/16", "10.0.0.0/8", "172.16.0.0/12",
                       "fc00::/7", "fe80::/10", "reject with icmpx admin-prohibited", "ct state established,related accept"):
            self.assertIn(needle, text)
        self.assertNotIn("100.64.0.0/10", text)  # CGNAT is not the LAN
        unit = (REPO / "platform/systemd/orbis-lan-only.service").read_text()
        self.assertIn("Before=network-pre.target", unit)
        self.assertIn("ConditionPathExists=!/etc/orbis/allow-internet", unit)
