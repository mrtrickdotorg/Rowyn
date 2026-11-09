#!/usr/bin/python3
"""Read-only Ubuntu runtime closure and Casper backing-device safety gate."""
import json
from pathlib import Path
import re
import subprocess
import sys

# Fixed programs used by the enabled install paths, including bootc helpers.
# The install-alongside-Windows (resize_ntfs) path additionally needs the
# ntfs-3g tools the executor's disk helper and the daemon's NTFS dirty-bit
# attestation invoke by absolute path (/usr/sbin/ntfsresize stages
# check/info/dry_run/resize, /usr/sbin/ntfsfix -n), plus /usr/sbin/btrfs
# for the subvolume layout prepare_btrfs_target creates on the new
# partition. The live image installs ntfs-3g and btrfs-progs explicitly
# (build/ubuntu-live.Containerfile); requiring them here makes a live
# image missing them fail in Prepare, before any disk mutation.
REQUIRED = (
    '/usr/bin/bootc', '/usr/bin/bootctl', '/usr/bin/ostree', '/usr/bin/skopeo',
    '/usr/bin/zstd', '/usr/bin/setpriv', '/usr/bin/systemctl',
    '/usr/bin/podman', '/usr/bin/crun',
    '/usr/bin/lsblk', '/usr/bin/findmnt', '/usr/bin/mount', '/usr/bin/umount',
    '/usr/sbin/blockdev', '/usr/bin/udevadm', '/usr/bin/openssl', '/usr/bin/mokutil',
    '/usr/bin/rowyn-cosign', '/usr/bin/rowyn-installer-exec',
    '/usr/sbin/blkid', '/usr/sbin/sgdisk', '/usr/sbin/parted',
    '/usr/sbin/mkfs.btrfs', '/usr/sbin/mkfs.fat', '/usr/sbin/useradd',
    '/usr/sbin/chpasswd', '/usr/sbin/losetup', '/usr/sbin/sfdisk', '/usr/sbin/fstrim',
    '/usr/sbin/ntfsresize', '/usr/sbin/ntfsfix', '/usr/sbin/btrfs',
)


def output(*args):
    return subprocess.check_output(args, text=True, timeout=15)


PREPARE_ROOT_CONFIGS = (
    "usr/lib/ostree/prepare-root.conf",
    "etc/ostree/prepare-root.conf",
)


def require_prepare_root_config(root=Path("/")):
    """bootc reads this policy from the running live root, even with --source-imgref."""
    root = Path(root)
    for relative in PREPARE_ROOT_CONFIGS:
        path = root / relative
        if path.is_file():
            return path
    expected = " or ".join("/" + path for path in PREPARE_ROOT_CONFIGS)
    raise ValueError("missing bootc prepare-root.conf in live root; expected " + expected)


def physical_disks(source, run=output, seen=None):
    """Resolve a block partition, stacked mapper, or loop backing to disks.

    Loop ISO boots (including Ventoy-style setups) may have their real media
    at /isodevice. The backing *file's* filesystem must be resolved, never the
    squashfs/overlay's pseudo-device. An unknown graph is not permission to wipe.
    """
    source = source.split('[', 1)[0]
    if not re.fullmatch(r'/dev/[A-Za-z0-9_./+-]+', source):
        raise ValueError('live source is not an identifiable block device: ' + source)
    seen = set() if seen is None else seen
    if source in seen:
        raise ValueError('cycle resolving live backing device')
    seen.add(source)
    records = json.loads(run('/usr/bin/lsblk', '--json', '--paths', '--inverse',
                             '--output', 'NAME,TYPE', source))['blockdevices']
    disks = set()
    def visit(record):
        name, kind = record.get('name'), record.get('type')
        if kind == 'disk':
            disks.add(name)
        elif kind == 'rom':
            disks.add(name)  # optical media still must not be selected
        elif kind == 'loop':
            loops = json.loads(run('/usr/sbin/losetup', '--json', '--output',
                                   'NAME,BACK-FILE', name))['loopdevices']
            if len(loops) != 1 or not loops[0].get('back-file', '').startswith('/'):
                raise ValueError('unresolved loop backing file')
            backing = loops[0]['back-file']
            device = run('/usr/bin/findmnt', '--noheadings', '--output', 'SOURCE',
                         '--target', backing).strip()
            disks.update(physical_disks(device, run, seen))
        for child in record.get('children', []):
            visit(child)
    for record in records:
        visit(record)
    if not disks:
        raise ValueError('cannot resolve source to physical media: ' + source)
    return disks


def assert_safe_target(target, run=output):
    protected = set()
    found = False
    # findmnt --mountpoint prevents an absent /cdrom resolving to overlay /.
    for mountpoint in ('/cdrom', '/isodevice'):
        try:
            source = run('/usr/bin/findmnt', '--noheadings', '--output', 'SOURCE',
                         '--mountpoint', mountpoint).strip()
        except subprocess.CalledProcessError as error:
            if error.returncode == 1:
                continue
            raise
        if not source:
            raise ValueError('empty Casper media source')
        found = True
        protected.update(physical_disks(source, run))
    if not found:
        raise ValueError('Casper physical live medium not found; refusing installation')
    target_disks = physical_disks(target, run)
    if protected & target_disks:
        raise ValueError('target overlaps the physical live medium')


def runtime_closure(root=Path("/"), runner=output, process_runner=subprocess.run):
    import os
    root = Path(root)

    def rooted(path):
        return root / path.lstrip('/')

    require_prepare_root_config(root)
    for binary in REQUIRED:
        if not os.access(rooted(binary), os.X_OK):
            raise ValueError('missing required Ubuntu runtime binary: ' + binary)
    for binary in ('/usr/bin/bootc', '/usr/bin/rowyn-installerd', '/usr/bin/rowyn-installer-shell'):
        result = process_runner([str(rooted('/usr/bin/ldd')), str(rooted(binary))],
                                text=True, capture_output=True, timeout=15)
        if result.returncode or 'not found' in result.stdout:
            raise ValueError('unresolved dynamic library closure: ' + binary)
    podman_version = runner(str(rooted('/usr/bin/podman')), '--version').strip()
    if not podman_version.startswith('podman version '):
        raise ValueError('podman version probe returned unexpected output: ' + podman_version)
    for subcommand in ('to-disk', 'to-filesystem'):
        help_text = runner(str(rooted('/usr/bin/bootc')), 'install', subcommand, '--help')
        for option in ('--source-imgref', '--target-imgref', '--bootloader'):
            if option not in help_text:
                raise ValueError('bootc CLI lacks ' + option)
    disk_help = runner(str(rooted('/usr/bin/bootc')), 'install', 'to-disk', '--help')
    if '--composefs-backend' not in disk_help:
        raise ValueError('bootc CLI lacks --composefs-backend')
    mount_help = runner(str(rooted('/usr/bin/bootc')), 'install', 'mount', '--help')
    for option in ('--sysroot', '--latest'):
        if option not in mount_help:
            raise ValueError('bootc install mount lacks ' + option)
    if not rooted('/usr/lib/systemd/boot/efi/systemd-bootx64.efi').is_file():
        raise ValueError('missing systemd-boot EFI binary')


def main():
    runtime_closure()
    if len(sys.argv) == 2 and sys.argv[1] == '--runtime-only':
        print('Ubuntu installer dynamic libraries, helpers and CLI closure passed')
        return
    if len(sys.argv) != 2:
        raise ValueError('expected one target disk')
    assert_safe_target(sys.argv[1])
    print('Ubuntu installer runtime and physical live-media protection passed')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError, KeyError) as error:
        sys.exit(str(error))
