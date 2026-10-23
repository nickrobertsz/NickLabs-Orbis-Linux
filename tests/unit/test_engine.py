"""Mode engine: planning rules and end-to-end transitions on a model of systemd."""

import unittest

from helpers import FixtureRoot

from orbis import config, paths
from orbis.engine import Engine
from orbis.systemd import default_fake


def make_engine(fx, vram=1024, gpu_clean=True, residue=None, drm=None, http=True, policy=None):
    sd = default_fake()
    sd.start("orbis-pc.target")
    cfg = config.load_config()
    cfg["orbis"]["stop_timeout_s"] = 0.2
    if policy:
        cfg["orbis"]["switch_policy"] = policy
    eng = Engine(sd, cfg,
                 sleep=lambda s: None,
                 http_ok=lambda url, t: http,
                 gpu_state=lambda: (gpu_clean, {} if gpu_clean else {"vm_fault": ["amdgpu: VM fault"]}),
                 vram_reader=lambda: vram,
                 snapshot=lambda: {"mem_total_kib": 6_900_000, "mem_available_kib": 5_000_000,
                                   "mem_used_kib": 1_900_000, "gpu": {}},
                 residue_fn=lambda pats, ign: list(residue or []),
                 drm_clients_fn=lambda: list(drm or []))
    return eng, sd


class PlanTests(unittest.TestCase):
    def test_pc_to_ai_is_fast_and_recommends_smaller_vram(self):
        with FixtureRoot() as fx:
            fx.boot_partition(vram="1024", bootargs="pci=nocrs intremap=off orbis.mode=pc")
            eng, sd = make_engine(fx, vram=1024)
            p = eng.plan("ai")
            self.assertEqual(p.from_mode, "pc")
            self.assertEqual(p.method, "fast")
            self.assertFalse(p.reboot_required)
            self.assertTrue(p.reboot_recommended)
            self.assertEqual(p.ram_returned_by_reboot_mib, 1024 - 128)
            self.assertIn("display-manager.service", p.will_stop)
            self.assertEqual(len(p.sessions_to_end), 1)
            kinds = {a["path"].rsplit("/", 1)[-1]: a["after"] for a in p.boot_actions}
            self.assertEqual(kinds["vram.txt"], "128")
            self.assertTrue(kinds["bootargs.txt"].endswith("orbis.mode=ai"))

    def test_ai_to_gaming_with_small_carveout_requires_reboot(self):
        with FixtureRoot() as fx:
            fx.boot_partition(vram="128")
            eng, sd = make_engine(fx, vram=128)
            p = eng.plan("gaming")
            self.assertTrue(p.reboot_required)
            self.assertEqual(p.method, "reboot")
            self.assertEqual(p.vram_next_mb, 2048)
            self.assertTrue(any("PS4 system software" in s for s in p.operator_steps))

    def test_fast_policy_refuses_when_reboot_required(self):
        with FixtureRoot() as fx:
            fx.boot_partition(vram="128")
            eng, _ = make_engine(fx, vram=128)
            p = eng.plan("gaming", policy="fast")
            self.assertEqual(p.method, "blocked")
            self.assertIn("refused", p.blocked)
            p = eng.plan("gaming", policy="fast", force=True)
            self.assertEqual(p.method, "fast")
            self.assertTrue(p.warnings)

    def test_unclean_gpu_forces_reboot_only_for_gpu_modes(self):
        with FixtureRoot() as fx:
            fx.boot_partition()
            eng, _ = make_engine(fx, gpu_clean=False)
            self.assertEqual(eng.plan("gaming").method, "reboot")
            self.assertEqual(eng.plan("ai", backend="cpu").method, "fast")      # headless CPU: GPU unused
            self.assertEqual(eng.plan("ai", backend="vulkan").method, "reboot")

    def test_vulkan_backend_needs_bigger_carveout(self):
        with FixtureRoot() as fx:
            fx.boot_partition(vram="128")
            eng, _ = make_engine(fx, vram=128)
            p = eng.plan("ai", backend="vulkan")
            self.assertTrue(p.reboot_required)
            self.assertEqual(p.vram_min_mb, 512)

    def test_noop_when_already_active(self):
        with FixtureRoot() as fx:
            fx.boot_partition()
            eng, _ = make_engine(fx)
            self.assertEqual(eng.plan("pc").method, "noop")

    def test_missing_boot_partition_is_a_warning(self):
        with FixtureRoot() as fx:
            eng, _ = make_engine(fx)
            p = eng.plan("ai")
            self.assertEqual(p.method, "fast")
            self.assertTrue(any("boot partition" in w for w in p.warnings))


class ExecuteTests(unittest.TestCase):
    def test_fast_switch_end_to_end(self):
        with FixtureRoot() as fx:
            bp = fx.boot_partition(vram="1024", bootargs="pci=nocrs intremap=off orbis.mode=pc")
            eng, sd = make_engine(fx, vram=1024)
            res = eng.execute(eng.plan("ai"))
            self.assertTrue(res.ok, res.message)
            self.assertEqual(sd.is_active("orbis-ai.target"), "active")
            self.assertEqual(sd.is_active("orbis-llama.service"), "active")
            self.assertEqual(sd.is_active("display-manager.service"), "inactive")
            self.assertEqual(sd.is_active("sshd.service"), "active")        # shared necessity survives
            self.assertEqual(sd.is_active("orbisd.service"), "active")
            self.assertEqual(sd.sessions(), [])
            self.assertEqual(paths.desired_mode_file().read_text().strip(), "ai")
            self.assertEqual((bp / "vram.txt").read_text().strip(), "128")
            self.assertIn("orbis.mode=ai", (bp / "bootargs.txt").read_text())
            self.assertTrue(paths.transitions_log().exists())
            self.assertEqual(eng.current_mode(), "ai")

    def test_round_trip_all_modes(self):
        with FixtureRoot() as fx:
            fx.boot_partition(vram="2048")
            eng, sd = make_engine(fx, vram=2048)
            for m in ("gaming", "ai", "pc", "ai", "gaming"):
                res = eng.execute(eng.plan(m))
                self.assertTrue(res.ok, f"{m}: {res.message}")
                self.assertEqual(eng.current_mode(), m)
                active_targets = [t for t in ("orbis-pc.target", "orbis-gaming.target", "orbis-ai.target")
                                  if sd.is_active(t) == "active"]
                self.assertEqual(active_targets, [f"orbis-{m}.target"])

    def test_reboot_path_stages_and_reboots(self):
        with FixtureRoot() as fx:
            bp = fx.boot_partition(vram="128")
            eng, sd = make_engine(fx, vram=128)
            res = eng.execute(eng.plan("gaming"))
            self.assertTrue(res.ok)
            self.assertTrue(sd.rebooted)
            self.assertEqual((bp / "vram.txt").read_text().strip(), "2048")
            self.assertEqual(sd.is_active("orbis-gaming.target"), "inactive")   # nothing started before reboot

    def test_leftover_process_offers_reboot(self):
        with FixtureRoot() as fx:
            fx.boot_partition()
            left = [{"pid": 4242, "comm": "Xorg", "unit": None, "pattern": "Xorg"}]
            eng, sd = make_engine(fx, residue=left)
            res = eng.execute(eng.plan("ai"))
            self.assertFalse(res.ok)
            self.assertTrue(res.offer_reboot)
            self.assertEqual(res.residue, left)

    def test_gpu_client_in_cpu_ai_mode_is_flagged(self):
        with FixtureRoot() as fx:
            fx.boot_partition()
            drm = [{"pid": 77, "comm": "Xorg", "unit": "display-manager.service", "vram_kib": 65536, "gtt_kib": 0}]
            eng, _ = make_engine(fx, drm=drm)
            res = eng.execute(eng.plan("ai", backend="cpu"))
            self.assertFalse(res.ok)
            self.assertEqual(res.gpu_residue[0]["comm"], "Xorg")

    def test_readiness_failure_is_reported(self):
        with FixtureRoot() as fx:
            fx.boot_partition()
            eng, _ = make_engine(fx, http=False)
            eng.profiles["ai"].readiness[0]["timeout_s"] = 0
            res = eng.execute(eng.plan("ai"))
            self.assertFalse(res.ok)
            self.assertIn("did not report ready", res.message)

    def test_blocked_plan_changes_nothing(self):
        with FixtureRoot() as fx:
            bp = fx.boot_partition(vram="128")
            eng, sd = make_engine(fx, vram=128)
            res = eng.execute(eng.plan("gaming", policy="fast"))
            self.assertFalse(res.ok)
            self.assertEqual((bp / "vram.txt").read_text(), "128")
            self.assertEqual(eng.current_mode(), "pc")
            self.assertFalse(paths.desired_mode_file().exists())


if __name__ == "__main__":
    unittest.main()
