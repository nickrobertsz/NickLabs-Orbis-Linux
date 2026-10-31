"""Reproduce the 2026-10-30 UID-1000 overlay regression without pacstrap."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


@unittest.skipUnless(os.geteuid() == 0, 'ownership regression requires an isolated root test runner')
class OverlayOwnershipTest(unittest.TestCase):
    def test_overlay_does_not_reown_root_or_service_data(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            overlay, root = base / 'overlay', base / 'rootfs'
            (overlay / 'etc').mkdir(parents=True)
            (overlay / 'etc/orbis.conf').write_text('security=enabled\n')
            (overlay / 'etc/target-link').symlink_to('/usr/lib/orbis')
            (root / 'etc').mkdir(parents=True)
            (root / 'var/lib/service').mkdir(parents=True)
            (root / 'var/lib/service/private').write_text('service-owned')
            try:
                os.chown(root / 'var/lib/service/private', 965, 965)
                os.chown(overlay, 1000, 1000)
                os.chown(overlay / 'etc', 1000, 1000)
                os.chown(overlay / 'etc/orbis.conf', 1000, 1000)
            except OSError as exc:
                self.skipTest(f'UID 965/1000 not mapped in this container: {exc}')
            subprocess.run(['cp', '-a', '--no-preserve=ownership', str(overlay) + '/.', str(root) + '/'], check=True)
            for anchor in (root, root / 'etc'):
                self.assertEqual((anchor.stat().st_uid, anchor.stat().st_gid), (0, 0))
            self.assertEqual((root / 'var/lib/service/private').stat().st_uid, 965)
            self.assertEqual((root / 'etc/orbis.conf').stat().st_uid, 0)
            self.assertEqual((root / 'etc/target-link').readlink(), Path('/usr/lib/orbis'))


if __name__ == '__main__':
    unittest.main()
