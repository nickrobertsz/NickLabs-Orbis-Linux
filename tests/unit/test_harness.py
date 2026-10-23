"""Inference harness: parsers, classification and crash-resume journal."""
import json
import tempfile
import unittest
from pathlib import Path

import helpers  # noqa: F401  (sets sys.path)
import orbis_infer_harness as h

KLD_OUT = """
====== KL divergence statistics ======
Mean    KLD:   0.004211 ±   0.000101
Maximum KLD:   0.910000
99.0%   KLD:   0.051000
====== Token probability statistics ======
RMS Δp    :  1.234 ± 0.010 %
Same top p: 98.765 ± 0.100 %
"""

EVAL = """common_debug_cb_eval:                     embd = (f32)   GET_ROWS(token_embd.weight{128, 320, 1, 1}, inp_tokens{8, 1, 1, 1}}) = {128, 8, 1, 1}
    [ ... ]
    sum = -26.900433
common_debug_cb_eval:                   attn_norm-0 = (f32)        MUL(norm-0{128, 8, 1, 1}, blk.0.attn_norm.weight{128, 1, 1, 1}}) = {128, 8, 1, 1}
    sum = 3.500000
"""


class HarnessTests(unittest.TestCase):
    def test_parse_kld(self):
        k = h.parse_kld(KLD_OUT)
        self.assertAlmostEqual(k["mean_kld"], 0.004211)
        self.assertAlmostEqual(k["kld_p99"], 0.051)
        self.assertAlmostEqual(k["same_top_p"], 98.765)

    def test_parse_and_compare_tensors(self):
        ref = h.parse_eval_callback(EVAL)
        self.assertEqual([r["name"] for r in ref], ["embd", "attn_norm-0"])
        cand = [dict(ref[0]), dict(ref[1], sum=3.9)]
        c = h.compare_tensors(ref, cand, 1e-3, 1e-3)
        self.assertEqual(c["first_divergence"]["name"], "attn_norm-0")
        self.assertEqual(c["bad_count"], 1)

    def test_classification_precedence(self):
        run = {"rc": 134, "kmsg": ["amdgpu 0000:00:01.0: amdgpu: [gfxhub] page fault (src_id:0 ring:24)"],
               "stderr": "ErrorDeviceLost", "stdout": ""}
        self.assertEqual(h.Campaign.classify(run, None)[0], "GPU_PAGE_FAULT")
        self.assertEqual(h.Campaign.classify({"rc": 1, "kmsg": [], "stderr": "ggml_vulkan: device Vulkan0 does not support 16-bit storage.", "stdout": ""}, None)[0],
                         "UNSUPPORTED_DEVICE")
        self.assertEqual(h.Campaign.classify({"rc": -9, "timed_out": True, "kmsg": [], "stderr": "", "stdout": ""}, None)[0], "TIMEOUT")
        self.assertEqual(h.Campaign.classify({"rc": 0, "kmsg": [], "stderr": "", "stdout": ""}, False)[0], "NUMERIC_MISMATCH")
        self.assertEqual(h.Campaign.classify({"rc": 0, "kmsg": [], "stderr": "", "stdout": ""}, True)[0], "PASS")

    def test_shader_scan(self):
        s = h.scan_shader_dump("BB0:\n  flat_load_dword v1, v[2:3]\n  buffer_store_dword v1, v0, s[0:3]\n")
        self.assertTrue(s["uses_flat"])
        self.assertEqual(s["instruction_counts"]["buffer_store"], 1)

    def test_resume_marks_orphans(self):
        d = Path(tempfile.mkdtemp())
        cfg = {"pins": {"model": "/nonexistent", "llama_cpu_bin_dir": "/x", "llama_vk_bin_dir": "/x"}}
        c = h.Campaign(cfg, d)
        c.journal.append({"event": "started", "step": "kld_c2048_ub512_nglall", "kind": "kld"})
        self.assertEqual(c.mark_interrupted(), ["kld_c2048_ub512_nglall"])
        recs = [r for r in c.journal.records() if r.get("event") == "finished"]
        self.assertEqual(recs[0]["outcome"], "HANG_OR_RESET_INFERRED")
        self.assertEqual(c.mark_interrupted(), [])

    def test_gate_refuses_without_hardware_evidence(self):
        d = Path(tempfile.mkdtemp())
        (d / "steps").mkdir()
        j = h.Journal(d / "journal.jsonl", h.Mirror(None))
        j.append({"event": "pins", "pins": {"vulkan_devices": []}})
        for c in (128, 256, 1024, 2048):
            j.append({"event": "finished", "step": f"kld_c{c}_ub512_nglall", "kind": "kld", "outcome": "PASS",
                      "params": {"ctx": c, "ubatch": 512, "ngl": "all"}, "kld": {}})
            (d / "steps" / f"kld_c{c}_ub512_nglall").mkdir()
            (d / "steps" / f"kld_c{c}_ub512_nglall" / "stderr.log").write_text("ggml_vulkan: 0 = llvmpipe (LLVM 20) | uma: 1\n")
        j.append({"event": "finished", "step": "server_mixed", "kind": "server", "outcome": "PASS"})
        j.append({"event": "finished", "step": "tensors_p32_nglall_cand", "kind": "tensors", "outcome": "PASS", "comparison": {}})
        g = h.gate(d, [128, 256], [1024, 2048])
        self.assertFalse(g["inference_gate"]["passed"])
        for c in (128, 256, 1024, 2048):
            (d / "steps" / f"kld_c{c}_ub512_nglall" / "stderr.log").write_text(
                "ggml_vulkan: 0 = AMD Radeon Graphics (RADV LIVERPOOL) (radv) | uma: 1\n")
        g = h.gate(d, [128, 256], [1024, 2048])
        self.assertTrue(g["inference_gate"]["passed"])
        self.assertEqual(g["inference_gate"]["max_prompt_tokens"], 2048)


if __name__ == "__main__":
    unittest.main()
