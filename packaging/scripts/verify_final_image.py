#!/usr/bin/env python3
"""Read-only Orbis release gate for the finished PS4 whole-disk image.

Runs inside the already-privileged build container. No mount or image write is
performed. Fails closed on mismatched boot payloads, ext4 contents or metadata.
"""
import argparse
import hashlib
import json
import pathlib
import re
import struct
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'image'))
import fat32


def need(condition, reason):
    if not condition:
        raise RuntimeError(reason)


def run(*args):
    p = subprocess.run(args, capture_output=True, check=False)
    need(p.returncode == 0, f'{args[0]} failed ({p.returncode}): {p.stderr.decode(errors="replace")[-400:]}')
    return p.stdout


def partitions(image):
    with image.open('rb') as f:
        sector = f.read(512)
    need(len(sector) == 512 and sector[-2:] == b'\x55\xaa', 'invalid image MBR')
    entries = []
    for n in range(4):
        _, _, kind, _, start, count = struct.unpack_from('<B3sB3sII', sector, 446 + n * 16)
        if kind and count:
            entries.append((kind, start * 512, count * 512))
    need(len(entries) == 2, 'expected exactly two partitions')
    boot = next(((off, length) for kind, off, length in entries if kind in (0x0B, 0x0C)), None)
    root = next(((off, length) for kind, off, length in entries if kind == 0x83), None)
    need(boot and root, 'expected FAT32 ORBIS-BOOT and Linux ORBIS-ROOT')
    spans = sorted([(boot[0], boot[0] + boot[1]), (root[0], root[0] + root[1])])
    need(spans[0][0] >= 512 and spans[0][1] <= spans[1][0] and spans[1][1] <= image.stat().st_size,
         'overlapping or out-of-range partitions')
    return boot, root


def ext4_cat(device, pathname):
    p = subprocess.run(['debugfs', '-R', 'cat ' + pathname, device], capture_output=True)
    need(p.returncode == 0 and not p.stderr.lower().startswith(b'cat:'),
         f'cannot read {pathname} from ext4: {p.stderr.decode(errors="replace")}')
    return p.stdout


def sha(data):
    return hashlib.sha256(data).hexdigest()


def verify(image, kernel, initramfs, manifest, profile):
    boot, root = partitions(image)
    files = fat32.read_root(image, boot[0])
    expected_kernel, expected_initrd = kernel.read_bytes(), initramfs.read_bytes()
    need(files.get('bzImage') == expected_kernel, 'boot partition bzImage differs from verified PS4 kernel')
    need(files.get('initramfs.cpio.gz') == expected_initrd, 'boot partition initramfs differs from built PS4 initramfs')
    meta = json.loads(files['orbis-boot.json'])
    need(meta['kernel']['sha256'] == sha(expected_kernel), 'boot manifest kernel hash mismatch')
    need(meta['initramfs']['sha256'] == sha(expected_initrd), 'boot manifest initramfs hash mismatch')
    need(meta['initramfs']['profile'] == profile, 'boot manifest profile mismatch')
    need(not meta.get('stage0'), 'full release was unexpectedly built as stage0')
    build = json.loads(manifest.read_text())
    release = build['kernel_release']
    need('orbis-ps4' in release, f'wrong kernel release {release}')
    need(build.get('target') == 'aeolia-belize-6.15', f'wrong PS4 kernel target {build.get("target")}')
    off, length = root
    dev = run('losetup', '--read-only', '--find', '--show', '--offset', str(off),
              '--sizelimit', str(length), str(image.resolve())).decode().strip()
    need(dev.startswith('/dev/loop'), 'losetup returned an unexpected device')
    try:
        label = run('blkid', '-s', 'LABEL', '-o', 'value', dev).decode().strip()
        need(label == 'ORBIS-ROOT', f'ext4 label {label!r} is not ORBIS-ROOT')
        result = subprocess.run(['e2fsck', '-fn', dev], capture_output=True)
        need(result.returncode == 0, f'ext4 filesystem check failed rc={result.returncode}: '
             + result.stdout.decode(errors='replace')[-400:])
        pkgs = dict(line.split(None, 1) for line in
                    ext4_cat(dev, '/usr/share/orbis/packages.txt').decode().splitlines() if len(line.split()) == 2)
        need('linux' not in pkgs, 'generic Arch linux package remains in PS4 image')
        need(pkgs.get('linux-orbis-ps4'), 'PS4 kernel package missing from image')
        need(pkgs.get('orbis-platform', '').startswith('0.2.0'), 'Orbis platform 0.2.0 missing')
        need(ext4_cat(dev, f'/usr/lib/modules/{release}/vmlinuz') == expected_kernel,
             'installed rootfs kernel differs from boot bzImage')
        need(ext4_cat(dev, '/usr/lib/orbis/initramfs/initramfs.cpio.gz') == expected_initrd,
             'installed rootfs PS4 initramfs differs from boot initramfs')
        for anchor in ('/', '/etc'):
            stat = run('debugfs', '-R', 'stat ' + anchor, dev).decode(errors='replace')
            own = re.search(r'User:\s*(\d+)\s+Group:\s*(\d+)', stat)
            need(own and own.group(1) == own.group(2) == '0', f'{anchor} is not root:root inside ext4')
    finally:
        run('losetup', '--detach', dev)
    return {'status': 'verified', 'kernel_release': release, 'profile': profile,
            'kernel_sha256': sha(expected_kernel), 'initramfs_sha256': sha(expected_initrd),
            'root_packages': len(pkgs), 'image': str(image)}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--image', type=pathlib.Path, required=True)
    ap.add_argument('--kernel', type=pathlib.Path, required=True)
    ap.add_argument('--initramfs', type=pathlib.Path, required=True)
    ap.add_argument('--kernel-manifest', type=pathlib.Path, required=True)
    ap.add_argument('--profile', required=True)
    args = ap.parse_args()
    try:
        report = verify(args.image, args.kernel, args.initramfs, args.kernel_manifest, args.profile)
    except (RuntimeError, ValueError, FileNotFoundError, KeyError, OSError) as exc:
        print(f'ORBIS FINAL IMAGE REFUSED: {exc}', file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
