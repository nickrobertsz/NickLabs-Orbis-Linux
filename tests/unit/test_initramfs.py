"""build_initramfs.py: required vs optional groups, verification, provenance, determinism."""
import gzip
import hashlib
import json
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "packaging/initramfs"))
import build_initramfs as B  # noqa: E402

FW = {
    "amdgpu/liverpool_pfp.bin": b"pfp" * 10,
    "amdgpu/liverpool_uvd.bin": b"uvd" * 10,
    "mrvl/sd8897_uapsta.bin": b"8897" * 10,
    "mrvl/sd8797_uapsta.bin": b"8797" * 10,
    "edid/1920x1080.bin": b"E" * 128,
}


def h(b):
    return hashlib.sha256(b).hexdigest()


class InitramfsBuilderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = self.d = Path(self.tmp.name)
        src = t / "src"
        (src / "bin").mkdir(parents=True)
        (src / "init").write_text("#!/bin/sh\necho upstream\n")
        (src / "bin/tool").write_text("x\n")
        (src / "README.md").write_text("excluded\n")
        subprocess.run(["git", "init", "-q", str(src)], check=True)
        subprocess.run(["git", "-C", str(src), "-c", "user.name=t", "-c", "user.email=t@t", "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(src), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x"],
                       check=True)
        self.commit = subprocess.run(["git", "-C", str(src), "rev-parse", "HEAD"], capture_output=True,
                                     text=True).stdout.strip()
        self.src = src
        (t / "sources.json").write_text(json.dumps({"initramfs": {"commit": self.commit}}))
        man = {"groups": {
            "gpu": {"redistributable": False, "files": [
                {"path": "amdgpu/liverpool_pfp.bin", "sha256": h(FW["amdgpu/liverpool_pfp.bin"])},
                {"path": "amdgpu/liverpool_uvd.bin", "sha256": h(FW["amdgpu/liverpool_uvd.bin"])}]},
            "m8897": {"redistributable": False, "files": [
                {"path": "mrvl/sd8897_uapsta.bin", "sha256": h(FW["mrvl/sd8897_uapsta.bin"])}]},
            "m8797": {"redistributable": False, "files": [
                {"path": "mrvl/sd8797_uapsta.bin", "sha256": h(FW["mrvl/sd8797_uapsta.bin"])}]},
            "edid": {"files": [{"path": "edid/1920x1080.bin", "sha256": h(FW["edid/1920x1080.bin"])}]},
            "unhashed": {"files": [{"path": "mrvl/sd8797_uapsta.bin", "sha256": None}]},
        }}
        (t / "fw.json").write_text(json.dumps(man))
        (t / "profiles.json").write_text(json.dumps({"profiles": {
            "console": {"required_groups": ["gpu", "m8897"], "optional_groups": ["m8797", "edid"]},
            "free": {"required_groups": [], "optional_groups": []},
            "opt-unhashed": {"required_groups": [], "optional_groups": ["unhashed"]}}}))
        self.patches = t / "patches"
        self.patches.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def fw_cpio(self, files: dict, name="community.cpio.gz") -> Path:
        ents = [("init", stat.S_IFREG | 0o755, b"#!/bin/sh\nmount LABEL=psxitarch /newroot\n")]
        for p, data in sorted(files.items()):
            ents.append(("lib/firmware/" + p, stat.S_IFREG | 0o644, data))
        out = self.d / name
        out.write_bytes(gzip.compress(B.write_newc(ents), mtime=0))
        return out

    def run_build(self, profile, fw=None, out="out/i.cpio.gz", **kw):
        return B.build(self.src, profile, self.d / out, firmware_from=fw,
                       manifest_path=self.d / "fw.json", profiles_path=self.d / "profiles.json",
                       sources_path=self.d / "sources.json", patches_dir=self.patches, check_script=None,
                       overlay_dir=kw.pop("overlay_dir", None), log=lambda s: None, **kw)

    def contents(self, out="out/i.cpio.gz"):
        return B.read_newc(gzip.decompress((self.d / out).read_bytes()))

    def test_profile_without_optional_edid_builds(self):
        fw = {k: v for k, v in FW.items() if not k.startswith("edid/")}
        rep = self.run_build("console", self.fw_cpio(fw))
        files = self.contents()
        self.assertIn("lib/firmware/mrvl/sd8897_uapsta.bin", files)
        self.assertIn("lib/firmware/mrvl/sd8797_uapsta.bin", files)
        self.assertNotIn("lib/firmware/edid/1920x1080.bin", files)
        self.assertEqual(rep["skipped_optional_groups"], [{"group": "edid", "reason": "not present in firmware source"}])
        self.assertFalse(rep["redistributable"])
        self.assertTrue(all(r["status"] == "verified" for r in rep["firmware"]))
        # only firmware is taken from the source initramfs, never its init
        self.assertEqual(files["init"][1], b"#!/bin/sh\necho upstream\n")
        self.assertNotIn("README.md", files)
        man = json.loads((self.d / "out/i.cpio.gz.manifest.json").read_text())
        self.assertEqual(man["output"]["sha256"], h((self.d / "out/i.cpio.gz").read_bytes()))
        self.assertEqual(man["firmware_source"]["sha256"], h((self.d / "community.cpio.gz").read_bytes()))
        self.assertTrue(man["source"]["pin_ok"])

    def test_missing_required_file_refuses_and_writes_nothing(self):
        fw = {k: v for k, v in FW.items() if k != "amdgpu/liverpool_uvd.bin"}
        with self.assertRaises(B.BuildError) as cm:
            self.run_build("console", self.fw_cpio(fw), allow_mismatch=True)
        self.assertIn("liverpool_uvd.bin", str(cm.exception))
        self.assertFalse((self.d / "out/i.cpio.gz").exists())

    def test_missing_required_wifi_refuses(self):
        fw = {k: v for k, v in FW.items() if k != "mrvl/sd8897_uapsta.bin"}
        with self.assertRaises(B.BuildError):
            self.run_build("console", self.fw_cpio(fw))

    def test_required_mismatch_refuses_unless_accepted_and_recorded(self):
        fw = dict(FW, **{"amdgpu/liverpool_pfp.bin": b"other revision"})
        with self.assertRaises(B.BuildError) as cm:
            self.run_build("console", self.fw_cpio(fw))
        self.assertIn("MISMATCH", str(cm.exception))
        rep = self.run_build("console", self.fw_cpio(fw), allow_mismatch=True)
        st = {r["path"]: r["status"] for r in rep["firmware"]}
        self.assertEqual(st["amdgpu/liverpool_pfp.bin"], "mismatch-accepted")

    def test_optional_present_but_mismatched_is_refused(self):
        fw = dict(FW, **{"edid/1920x1080.bin": b"different edid"})
        with self.assertRaises(B.BuildError):
            self.run_build("console", self.fw_cpio(fw))

    def test_optional_without_recorded_hash_is_not_silently_included(self):
        with self.assertRaises(B.BuildError) as cm:
            self.run_build("opt-unhashed", self.fw_cpio(FW))
        self.assertIn("UNVERIFIED", str(cm.exception))

    def test_firmware_free_profile_is_redistributable(self):
        rep = self.run_build("free")
        files = self.contents()
        self.assertFalse([k for k, (m, c) in files.items() if k.startswith("lib/firmware/") and c])
        self.assertTrue(rep["redistributable"])

    def test_required_profile_needs_a_firmware_source(self):
        with self.assertRaises(B.BuildError):
            self.run_build("console")

    def test_unknown_profile(self):
        with self.assertRaises(B.BuildError):
            self.run_build("nope", self.fw_cpio(FW))

    def test_unpinned_source_refused(self):
        (self.d / "sources.json").write_text(json.dumps({"initramfs": {"commit": "0" * 40}}))
        with self.assertRaises(B.BuildError):
            self.run_build("free")
        self.run_build("free", allow_unpinned=True)

    def test_deterministic(self):
        src = self.fw_cpio(FW)
        self.run_build("console", src, out="a.gz")
        self.run_build("console", src, out="b.gz")
        self.assertEqual((self.d / "a.gz").read_bytes(), (self.d / "b.gz").read_bytes())

    def test_patches_applied_to_a_copy(self):
        (self.patches / "0001-x.patch").write_text(
            "--- a/init\n+++ b/init\n@@ -1,2 +1,2 @@\n #!/bin/sh\n-echo upstream\n+echo orbis\n")
        rep = self.run_build("free")
        self.assertEqual(self.contents()["init"][1], b"#!/bin/sh\necho orbis\n")
        self.assertEqual((self.src / "init").read_text(), "#!/bin/sh\necho upstream\n")
        self.assertEqual(self.contents()["GIT_HASH"][1], (self.commit[:7] + "+orbis1\n").encode())
        self.assertEqual(rep["patches"][0]["file"], "0001-x.patch")

    def test_bad_patch_refuses(self):
        (self.patches / "0001-bad.patch").write_text(
            "--- a/init\n+++ b/init\n@@ -1,2 +1,2 @@\n #!/bin/sh\n-echo nothing-like-this\n+echo orbis\n")
        with self.assertRaises(B.BuildError):
            self.run_build("free")

    def test_overlay_adds_files_but_never_replaces_upstream(self):
        ov = self.d / "overlay"
        (ov / "bin").mkdir(parents=True)
        (ov / "bin/orbis-tool").write_text("#!/bin/sh\n")
        rep = self.run_build("free", overlay_dir=ov)
        files = self.contents()
        self.assertEqual(files["bin/orbis-tool"][0] & 0o777, 0o755)
        self.assertEqual(rep["overlay"][0]["path"], "bin/orbis-tool")
        (ov / "init").write_text("#!/bin/sh\necho hijack\n")
        with self.assertRaises(B.BuildError):
            self.run_build("free", overlay_dir=ov)

    def test_concatenated_cpio_later_entry_wins(self):
        a = B.write_newc([("x", stat.S_IFREG | 0o644, b"loader")])
        b = B.write_newc([("x", stat.S_IFREG | 0o644, b"initramfs")])
        self.assertEqual(B.read_newc(a + b)["x"][1], b"initramfs")


class RealManifestsTest(unittest.TestCase):
    def test_cuh_1215a_profile_is_consistent(self):
        man = json.loads((REPO / "manifests/firmware.json").read_text())
        prof = json.loads((REPO / "manifests/initramfs-profiles.json").read_text())["profiles"]
        p = prof["cuh-1215a"]
        self.assertEqual(p["required_groups"], ["liverpool-gpu", "marvell-88w8897"])
        self.assertIn("edid", p["optional_groups"])
        for g in p["required_groups"] + p["optional_groups"]:
            self.assertIn(g, man["groups"])
            for f in man["groups"][g]["files"]:
                self.assertRegex(f["sha256"], r"^[0-9a-f]{64}$", f"{g}:{f['path']} must be pinned")
        self.assertEqual(len(man["groups"]["liverpool-gpu"]["files"]), 10)
        for name, g in man["groups"].items():
            self.assertIn("redistributable", g, name)

    def test_references_pinned(self):
        refs = json.loads((REPO / "manifests/references.json").read_text())
        for a in refs["artifacts"]:
            self.assertRegex(a["sha256"], r"^[0-9a-f]{64}$")
            self.assertFalse(a["redistribute"])


if __name__ == "__main__":
    unittest.main()
