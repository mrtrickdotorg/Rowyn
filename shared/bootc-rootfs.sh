#!/usr/bin/env bash

set -xeuo pipefail

# Deliberate: the payload keeps only /usr; everything else is recreated at boot.
# shellcheck disable=SC2114
rm -rf /home /root /usr/local /srv /opt /mnt /usr/lib/sysimage/log /usr/lib/sysimage/cache/pacman/pkg

# Empty /boot and /var in place instead of deleting and recreating
# them: a recreated directory was exported as an opaque-directory
# whiteout on some builds and as per-entry whiteouts on others.
find /boot /var -mindepth 1 -delete

mkdir -p /sysroot /usr/lib/ostree

ln -sT sysroot/ostree /ostree && ln -sT var/roothome /root && ln -sT var/srv /srv && ln -sT var/opt /opt && ln -sT var/mnt /mnt && ln -sT var/home /home && ln -sT ../var/usrlocal /usr/local

echo "$(for dir in opt home srv mnt usrlocal ; do echo "d /var/$dir 0755 root root -" ; done)" | tee -a "/usr/lib/tmpfiles.d/bootc-base-dirs.conf"

printf "d /var/roothome 0700 root root -\nd /run/media 0755 root root -" | tee -a "/usr/lib/tmpfiles.d/bootc-base-dirs.conf"

printf '[composefs]\nenabled = yes\n[sysroot]\nreadonly = true\n' | tee "/usr/lib/ostree/prepare-root.conf"

