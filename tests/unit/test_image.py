"""USB image: built-in FAT32 writer, stage-0 images, determinism. Needs dosfstools (skipped otherwise)."""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "packaging/image"))
import fat32  # noqa: E402
from helpers import make_bzimage  # noqa: E402

MKFS = shutil.which("mkfs.fat")
FSCK = shutil.which("fsck.fat")


@unittest.skipUnless(MKFS, "dosfstools (mkfs.fat) not installed")
class Fat32WriterTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def fresh(self, size_mb=64, spc="1"):
        img = self.d / "fat.img"
        with open(img, "wb") as f:
            f.truncate(size_mb << 20)
        subprocess.run([MKFS, "-F", "32", "-s", spc, "-n", "ORBIS-BOOT", "-i", "4F524253", "--invariant", str(img)],
                       check=True, capture_output=True)
        return img

    def src(self, name, data):
        p = self.d / name
        p.write_bytes(data)
        return p

    def test_long_names_round_trip_and_fsck_clean(self):
        img = self.fresh()
        files = [("bzImage", self.src("k", b"K" * 70000)),
                 ("initramfs.cpio.gz", self.src("i", bytes(range(256)) * 900)),
                 ("bootargs.txt", self.src("b", b"root=LABEL=ORBIS-ROOT orbis.mode=pc\n")),
                 ("vram.txt", self.src("v", b"1024\n")),
                 ("EMPTY.TXT", self.src("e", b"")),
                 ("exactly-thirteen", self.src("x", b"13")),
                 ("bzImage.orbis-prev", self.src("p", b"P" * 513))]
        res = fat32.add_files(img, 0, files)
        self.assertEqual(len({r["short"] for r in res}), len(files))
        got = fat32.read_root(img, 0)
        for name, p in files:
            self.assertEqual(got[name], p.read_bytes(), name)
        if FSCK:
            r = subprocess.run([FSCK, "-n", str(img)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_root_directory_grows(self):
        img = self.fresh()
        files = [(f"a-rather-long-file-name-{i:03d}.data", self.src(f"f{i}", bytes([i]) * (i + 1))) for i in range(80)]
        fat32.add_files(img, 0, files)
        got = fat32.read_root(img, 0)
        self.assertEqual(len([n for n in got if n.startswith("a-rather")]), 80)
        if FSCK:
            self.assertEqual(subprocess.run([FSCK, "-n", str(img)], capture_output=True).returncode, 0)

    def test_duplicate_refused(self):
        img = self.fresh()
        fat32.add_files(img, 0, [("vram.txt", self.src("v", b"1024\n"))])
        with self.assertRaises(fat32.FatError):
            fat32.add_files(img, 0, [("VRAM.TXT", self.src("v2", b"512\n"))])

    def test_no_space(self):
        img = self.fresh(size_mb=33)
        with self.assertRaises(fat32.FatError):
            fat32.add_files(img, 0, [("big", self.src("big", b"x" * (40 << 20)))])

    def test_deterministic(self):
        a = self.fresh()
        fat32.add_files(a, 0, [("bzImage", self.src("k", b"K" * 5000))])
        da = a.read_bytes()
        b = self.fresh()
        fat32.add_files(b, 0, [("bzImage", self.src("k", b"K" * 5000))])
        self.assertEqual(da, b.read_bytes())

    def test_not_fat32(self):
        p = self.src("zero.img", b"\0" * 4096)
        with self.assertRaises(fat32.FatError):
            fat32.read_root(p, 0)


@unittest.skipUnless(MKFS and shutil.which("mke2fs"), "dosfstools/e2fsprogs not installed")
class Stage0ImageTest(unittest.TestCase):
    def test_stage0_image_from_synthetic_kernel(self):
        with tempfile.TemporaryDirectory() as t:
            t = Path(t)
            kernel = t / "bzImage"
            kernel.write_bytes(make_bzimage())
            ird = t / "initramfs.cpio.gz"
            ird.write_bytes(b"\x1f\x8b" + b"\0" * 64)
            (t / "initramfs.cpio.gz.manifest.json").write_text(json.dumps(
                {"redistributable": True, "profile": {"name": "firmware-free"}, "firmware": []}))
            out = t / "s0.img"
            r = subprocess.run([sys.executable, str(REPO / "packaging/image/make_usb_image.py"), "image", "--stage0",
                                "--kernel", str(kernel), "--initramfs", str(ird), "--size", "800M", "-o", str(out)],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            plan = json.loads(r.stdout)
            self.assertIn("orbis.stage0=1", plan["bootargs"])
            self.assertTrue(plan["boot"]["redistributable"])
            got = fat32.read_root(out, plan["layout_sectors"]["boot"][0] * 512)
            self.assertEqual(got["bootargs.txt"].decode().count("\n"), 1)
            self.assertEqual(got["vram.txt"], b"1024\n")
            self.assertEqual(json.loads(got["orbis-boot.json"])["initramfs"]["profile"], "firmware-free")
            r2 = subprocess.run([sys.executable, str(REPO / "packaging/image/make_usb_image.py"), "image", "--stage0",
                                 "--kernel", str(kernel), "--initramfs", str(ird), "--size", "800M",
                                 "-o", str(t / "s1.img")], capture_output=True, text=True)
            self.assertEqual(json.loads(r2.stdout)["sha256"], plan["sha256"])

    def test_full_image_needs_rootfs(self):
        with tempfile.TemporaryDirectory() as t:
            t = Path(t)
            kernel = t / "bzImage"
            kernel.write_bytes(make_bzimage())
            (t / "i.gz").write_bytes(b"x")
            r = subprocess.run([sys.executable, str(REPO / "packaging/image/make_usb_image.py"), "image",
                                "--kernel", str(kernel), "--initramfs", str(t / "i.gz"), "-o", str(t / "o.img")],
                               capture_output=True, text=True)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("--stage0", r.stderr)


if __name__ == "__main__":
    unittest.main()
