import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from helpers import REPO, FixtureRoot, make_bzimage

from orbis import bootfs, bzimage


class BzImageTests(unittest.TestCase):
    def _write(self, data: bytes) -> Path:
        f = Path(tempfile.mkstemp(suffix=".bzImage")[1])
        f.write_bytes(data)
        return f

    def test_protocol_215_with_64bit_entry_is_accepted(self):
        rep = bzimage.inspect(self._write(make_bzimage()))
        self.assertTrue(rep.ok, rep.errors)
        self.assertEqual(rep.protocol, (2, 15))
        self.assertEqual(rep.cmdline_size, 2047)
        self.assertIn("6.15.4-orbis-ps4", rep.kernel_version)

    def test_protocol_213_is_refused(self):
        rep = bzimage.inspect(self._write(make_bzimage(protocol=(2, 13))))
        self.assertFalse(rep.ok)
        self.assertTrue(any("hard-locked" in e for e in rep.errors))

    def test_unknown_protocol_only_warns(self):
        rep = bzimage.inspect(self._write(make_bzimage(protocol=(2, 16))))
        self.assertTrue(rep.ok)
        self.assertTrue(rep.warnings)

    def test_missing_64bit_entry_refused(self):
        rep = bzimage.inspect(self._write(make_bzimage(xloadflags=0)))
        self.assertFalse(rep.ok)

    def test_not_a_bzimage(self):
        rep = bzimage.inspect(self._write(make_bzimage(magic=b"XXXX")))
        self.assertFalse(rep.ok)

    @unittest.skipUnless(os.environ.get("ORBIS_TEST_BZIMAGE"), "set ORBIS_TEST_BZIMAGE to a real PS4 kernel")
    def test_real_ps4_kernel(self):
        rep = bzimage.inspect(os.environ["ORBIS_TEST_BZIMAGE"])
        self.assertTrue(rep.ok, rep.errors)
        self.assertEqual(rep.ikconfig.get("CONFIG_X86_PS4"), "y")


class BootPartitionTests(unittest.TestCase):
    def test_vram_parsing_mirrors_loader(self):
        with FixtureRoot() as fx:
            bp = bootfs.BootPartition(fx.boot_partition(vram=None))
            self.assertEqual(bp.vram_mb(), 1024)           # missing -> default
            for text, want in (("2048\n", 2048), ("128", 128), ("abc", 1024), ("20", 1024),
                               ("5000", 1024), ("256MB", 256)):
                (bp.root / "vram.txt").write_text(text)
                self.assertEqual(bp.vram_mb(), want, text)

    def test_vram_validation(self):
        self.assertEqual(bootfs.validate_vram(1024), [])
        self.assertTrue(bootfs.validate_vram(3072))          # not a power of two
        self.assertTrue(bootfs.validate_vram(16))            # below loader minimum
        self.assertTrue(bootfs.validate_vram(4096, [128, 1024]))

    def test_set_mode_preserves_platform_args_and_keeps_previous(self):
        with FixtureRoot() as fx:
            root = fx.boot_partition(bootargs="pci=nocrs intremap=off root=LABEL=ORBIS-ROOT orbis.mode=pc\nIGNORED\n")
            bp = bootfs.BootPartition(root)
            bp.set_mode("ai")
            line = (root / "bootargs.txt").read_text()
            self.assertEqual(line, "pci=nocrs intremap=off root=LABEL=ORBIS-ROOT orbis.mode=ai\n")
            self.assertIn("orbis.mode=pc", (root / "bootargs.txt.orbis-prev").read_text())
            self.assertEqual(bp.status()["next_mode"], "ai")

    def test_default_cmdline_used_when_no_bootargs(self):
        with FixtureRoot() as fx:
            bp = bootfs.BootPartition(fx.boot_partition())
            bp.set_mode("gaming")
            txt = (bp.root / "bootargs.txt").read_text()
            self.assertIn("video=HDMI-A-1:1920x1080@60", txt)
            self.assertTrue(txt.strip().endswith("orbis.mode=gaming"))

    def test_dry_run_changes_nothing(self):
        with FixtureRoot() as fx:
            root = fx.boot_partition(vram="1024")
            bp = bootfs.BootPartition(root)
            bp.set_vram(128, [128, 1024], dry_run=True)
            self.assertEqual((root / "vram.txt").read_text(), "1024")
            self.assertEqual(len(bp.actions), 1)

    def test_kernel_install_and_rollback(self):
        with FixtureRoot() as fx:
            root = fx.boot_partition()
            old = (root / "bzImage").read_bytes()
            new = Path(tempfile.mkstemp()[1]); new.write_bytes(make_bzimage(version="6.15.9-new"))
            ird = Path(tempfile.mkstemp()[1]); ird.write_bytes(b"\x1f\x8b new")
            bp = bootfs.BootPartition(root)
            bp.install_kernel_pair(new, ird)
            self.assertIn(b"6.15.9-new", (root / "bzImage").read_bytes())
            self.assertEqual((root / "bzImage.orbis-prev").read_bytes(), old)
            bp.rollback_kernel_pair()
            self.assertEqual((root / "bzImage").read_bytes(), old)
            self.assertFalse(list(root.glob("*.orbis-new")))

    def test_bad_kernel_never_installed(self):
        with FixtureRoot() as fx:
            root = fx.boot_partition()
            bad = Path(tempfile.mkstemp()[1]); bad.write_bytes(make_bzimage(protocol=(2, 13)))
            ird = Path(tempfile.mkstemp()[1]); ird.write_bytes(b"x")
            with self.assertRaises(bootfs.BootFsError):
                bootfs.BootPartition(root).install_kernel_pair(bad, ird)
            self.assertEqual((root / "bzImage").read_bytes(), make_bzimage())


class GeneratorTests(unittest.TestCase):
    GEN = REPO / "platform/generators/orbis-mode-generator"

    def _run(self, cmdline="", desired=None, default=None):
        r = Path(tempfile.mkdtemp())
        (r / "proc").mkdir()
        (r / "proc/cmdline").write_text(cmdline + "\n")
        units = r / "usr/lib/systemd/system"
        units.mkdir(parents=True)
        for m in ("pc", "gaming", "ai"):
            (units / f"orbis-{m}.target").write_text("[Unit]\n")
        if desired:
            (r / "var/lib/orbis").mkdir(parents=True)
            (r / "var/lib/orbis/desired-mode").write_text(desired + "\n")
        if default:
            (r / "etc/orbis").mkdir(parents=True)
            (r / "etc/orbis/default-mode").write_text(default + "\n")
        early = r / "early"
        subprocess.run(["sh", str(self.GEN), str(r / "normal"), str(early), str(r / "late")],
                       check=True, env={"ORBIS_GENERATOR_ROOT": str(r), "PATH": os.environ["PATH"]})
        link = early / "default.target"
        return os.readlink(link) if link.is_symlink() else None

    def test_precedence(self):
        self.assertTrue(self._run("quiet orbis.mode=ai", desired="gaming").endswith("orbis-ai.target"))
        self.assertTrue(self._run("quiet", desired="gaming", default="ai").endswith("orbis-gaming.target"))
        self.assertTrue(self._run("quiet", default="ai").endswith("orbis-ai.target"))
        self.assertTrue(self._run("quiet").endswith("orbis-pc.target"))
        self.assertTrue(self._run("orbis.mode=bogus").endswith("orbis-pc.target"))
        self.assertIsNone(self._run("systemd.unit=rescue.target orbis.mode=ai"))


if __name__ == "__main__":
    unittest.main()
