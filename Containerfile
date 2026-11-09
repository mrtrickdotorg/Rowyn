# Rowyn — a bootc-based Ubuntu (latest release, non-LTS) shipping KDE Plasma.
#
# Structure adapted from Bootcrew's ubuntu-bootc (Apache-2.0; see NOTICE and
# the vendored scripts under shared/). Differences from upstream Bootcrew:
#   - base is the latest Ubuntu release (26.10, digest-pinned), not 25.10 "questing"
#   - Rowyn identity in /usr/lib/os-release
#   - no baked-in login users: the base image's `ubuntu` account is
#     removed, and the only interactive account on an installed system is
#     the one the installer creates
#   - SSH is installed but disabled, and an opted-in sshd is key-only
#
# Build:  build/fetch-assets.sh        (one-time, needs network)
#         sudo podman build -t rowyn:local .   (fully offline)
#
# All network inputs (pinned Rust toolchain tarball, bootc source with
# vendored crate deps) are fetched on the host into build-assets/ first —
# the build itself never touches the network.
ARG RUST_VERSION=1.99.0

FROM scratch AS ctx
COPY shared/ /shared
COPY keys/ /keys
COPY build-assets/ /assets

# Digest-pinned; must equal UBUNTU_LIVE_IMAGE in .github/workflows/installer-iso.yml
# (build/ubuntu-only-check.py enforces this). The guard makes a wrong digest fail fast.
FROM docker.io/library/ubuntu@sha256:262df65c0ec6bf8d9dcc87454f4330a05fae4b97825f45854af3b136666584c2 AS base
RUN . /etc/os-release && test "$ID" = ubuntu && test "$VERSION_ID" = 26.10

# USN-8996-1 (CVE-2026-109518): no published Ubuntu 26.10 image carries the APT
# fix yet, and every layer below installs packages over the image's plain-HTTP
# archive sources with the default Acquire::Retries. Take the fixed apt from the
# security archive before anything else runs, and fail closed if it is absent.
RUN apt-get update && \
    apt-get install -y --only-upgrade apt && \
    installed=$(dpkg-query -W -f='${Version}' apt) && \
    dpkg --compare-versions "$installed" ge 3.3.3ubuntu0.1 && \
    rm -rf /var/lib/apt/lists/*

# --- builder: compile bootc from source -------------------------------------
FROM base AS builder
ARG RUST_VERSION
RUN --mount=type=tmpfs,dst=/tmp --mount=type=tmpfs,dst=/root --mount=type=tmpfs,dst=/boot \
    apt update -y && \
    apt install -y python3 git curl make build-essential go-md2man libzstd-dev pkgconf libostree-dev ostree xz-utils libclang-dev

WORKDIR /home/build
RUN --mount=type=bind,from=ctx,source=/,target=/ctx \
    mkdir -p /tmp/rust-dist && \
    tar -xJf "/ctx/assets/rust-${RUST_VERSION}-x86_64-unknown-linux-gnu.tar.xz" -C /tmp/rust-dist --strip-components=1 && \
    /tmp/rust-dist/install.sh --prefix=/opt/rust --without=rust-docs && \
    cp -a /ctx/assets/bootc /home/build/bootc && \
    /usr/bin/python3 /ctx/shared/bootc-sigpolicy-patch.py /home/build/bootc && \
    PATH=/opt/rust/bin:$PATH /ctx/shared/build.sh /home/build/bootc

# --- hub builder: compile Rowyn Hub from the pinned, ported upstream source -----
# The ported tree is produced outside the build (CI runs
# build/ubuntu-hub-source.py against the pinned upstream commit into hub-source/;
# see .github/workflows/build.yml) so this stage only compiles. Unlike the
# offline bootc stage it needs network for npm/cargo registries, mirroring
# the upstream hub-web-builder stage.
# Layout mirrors that upstream stage on purpose: rowyn-hub-web and
# rowyn-shared-rs sit one level shallower than in the upstream repo, so the shared
# crate's repo-relative include of build_files/exe-handler-apps.json lands at
# /build_files and the glib patch path lands at /third_party. Copy those two
# inputs to match rather than editing the pinned crate.
FROM base AS hub-builder
RUN --mount=type=tmpfs,dst=/tmp --mount=type=tmpfs,dst=/root \
    apt update -y && \
    apt install -y --no-install-recommends \
        build-essential cargo rustc nodejs npm pkg-config \
        libwebkit2gtk-4.1-dev libgtk-3-dev libsoup-3.0-dev \
        libayatana-appindicator3-dev librsvg2-dev libdbus-1-dev \
        libssl-dev libxdo-dev && \
    rm -rf /var/lib/apt/lists/*
COPY hub-source/src/rowyn-hub-web /build/rowyn-hub-web
COPY hub-source/src/rowyn-shared-rs /build/rowyn-shared-rs
COPY hub-source/build_files/exe-handler-apps.json /build_files/exe-handler-apps.json
COPY hub-source/third_party/gtk-glib-0.18.5 /third_party/gtk-glib-0.18.5
WORKDIR /build/rowyn-hub-web
RUN npm ci && npm run build
WORKDIR /build/rowyn-hub-web/src-tauri
RUN cargo build --release --locked && \
    cp target/release/rowyn-hub-shell /rowyn-hub-shell

# --- system: assemble the bootable image ------------------------------------
FROM base AS system
ARG ROWYN_SOURCE_REVISION=unknown
ARG ROWYN_IMAGE_VERSION=0.1
LABEL org.opencontainers.image.source="https://github.com/mrtrickdotorg/Rowyn"
LABEL org.opencontainers.image.revision="${ROWYN_SOURCE_REVISION}"
COPY --from=builder /output /

RUN --mount=type=tmpfs,dst=/tmp --mount=type=tmpfs,dst=/root --mount=type=tmpfs,dst=/boot apt update -y && \
  apt install -y btrfs-progs bubblewrap dosfstools e2fsprogs fdisk linux-firmware linux-image-generic netplan.io openssh-server systemd-resolved chrony skopeo podman crun sudo systemd systemd-boot* xfsprogs libostree-dev dracut && \
  cp /boot/vmlinuz-* "$(find /usr/lib/modules -mindepth 1 -maxdepth 1 -type d | sort -V | tail -n 1)/vmlinuz" && \
  apt clean -y

# The official Ubuntu base image ships an `ubuntu` login user (uid 1000).
# An installed Rowyn system must offer only the account the installer
# creates, so the baked-in account (and its home) goes away here, before
# any layer that could come to depend on it.
RUN userdel --remove ubuntu

# --- KDE Plasma package layer -------------------------------------------------
# Install packages that still need the distro's dpkg database before
# bootc-rootfs.sh removes /var. Desktop enablement remains below rootfs
# normalization because it only writes final system configuration.
#
# The desktop set is the Kubuntu default applications and integration
# (the set the live image carries from the Kubuntu manifest), named
# explicitly rather than pulled via the kubuntu-desktop metapackage so
# the deliberate exclusions stay excluded: no LibreOffice, no
# Firefox/Thunderbird (snaps; Rowyn is snap-free), no plasma-welcome
# (purged below), no legacy Oxygen/Air themes, no *-doc packages.
# The command-line backbone is the stock Ubuntu one — the
# ubuntu-minimal + ubuntu-standard metapackages — plus the basic
# tools ubuntu-standard does not pull (curl, vim-tiny, htop,
# iputils-ping, netcat-openbsd, rfkill, plocate, pastebinit),
# named explicitly so they cannot quietly drop out of the set.
#
# Performance group (end of the first list): kwin-wayland +
# plasma-workspace carry the Plasma Wayland session (SDDM lists Wayland
# sessions first, so it is the preselected default) while xserver-xorg +
# plasma-session-x11 keep the X11 session as fallback;
# power-profiles-daemon, systemd-zram-generator, gamemode and
# steam-devices (multiverse) are host plumbing for shared/performance-defaults.sh.
# mesa-vulkan-drivers + vulkan-tools are named explicitly so the host
# Vulkan stack is guaranteed by the package set, not inherited by
# dependency closure (the Flatpak gaming route carries its own runtimes;
# the host stack is what native games and tools see).
#
# Daily-driver fills (Nov 2026): plasma-systemmonitor (task manager),
# kamoso (camera), fprintd (fingerprint reader support; PAM login wiring
# is left to the user), samba + smbclient + kdenetwork-filesharing
# (Windows file sharing, client and server), kaccounts-integration +
# kio-gdrive (KDE Online Accounts / Google Drive in Dolphin — the
# accounts the exe-handler already points OneDrive/Google users at),
# and krdp (KDE remote desktop server).
#
# NVIDIA group (last install): Canonical's prebuilt, signed open kernel
# modules (linux-modules-nvidia-615-open-generic pulls the module package
# for exactly the kernel linux-image-generic installed, so no DKMS and
# nothing compiles on the user's machine) plus the matching userspace. The
# nvidia-driver-615-open metapackage is deliberately not used: it hard-depends
# on nvidia-dkms-615-open and dkms. Wayland (libnvidia-egl-wayland1 via
# libnvidia-gl) and the X11 fallback session (xserver-xorg-video-nvidia) are
# covered. Whether nvidia or nouveau binds is decided at every boot by
# shared/gpu-select.sh. The supported-GPU list the decision needs lives under
# /usr/share/doc, which the base image's dpkg excludes drop, hence the
# path-include (removed again once the ID list is generated below).
RUN printf 'path-include=/usr/share/doc/nvidia-kernel-common-*/supported-gpus.json\n' \
        > /etc/dpkg/dpkg.cfg.d/rowyn-nvidia-gpus && \
    apt-get update && \
    apt-get install -y \
        plasma-desktop sddm xserver-xorg sddm-theme-breeze konsole dolphin kinfocenter kde-spectacle \
        breeze breeze-gtk-theme breeze-icon-theme kde-style-breeze kde-cli-tools \
        plasma-nm plasma-pa xdg-desktop-portal-kde \
        bluedevil kscreen powerdevil \
        kup-backup bup rsync kde-config-flatpak libnotify-bin \
        network-manager pipewire wireplumber upower udisks2 plymouth plymouth-label \
        tzdata tzdata-legacy polkitd \
        fonts-noto-core \
        libwebkit2gtk-4.1-0 libayatana-appindicator3-1 libxdo3 \
        openconnect vpnc-scripts ksshaskpass firewalld \
        gwenview okular okular-extra-backends ark kate kcalc kcharselect \
        kdeconnect elisa haruna skanpage unzip zip \
        plasma-systemmonitor kamoso fprintd \
        kaccounts-integration kio-gdrive kdenetwork-filesharing krdp \
        cups cups-browsed cups-client cups-daemon cups-filters cups-ipp-utils \
        cups-pk-helper print-manager system-config-printer-common \
        system-config-printer-udev bluez-cups colord kde-config-tablet \
        ipp-usb avahi-daemon \
        sane-airscan sane-utils \
        samba smbclient \
        kio-admin kio-audiocd kde-config-updates kde-config-plymouth \
        plasma-runners-addons plasma-widgets-addons plasma-wallpapers-addons \
        plasma-calendar-addons plasma-dataengines-addons kwin-addons \
        ffmpeg gstreamer1.0-pipewire phonon4qt6-backend-vlc \
        fonts-liberation fonts-dejavu-core fonts-dejavu-mono \
        orca \
        ubuntu-minimal ubuntu-standard \
        locales pciutils fwupd \
        curl wget nano vim-tiny htop file lshw bind9-dnsutils iputils-ping \
        iputils-tracepath mtr-tiny netcat-openbsd lsof strace tcpdump hdparm \
        rfkill plocate manpages bash-completion command-not-found pastebinit \
        kwin-wayland plasma-workspace plasma-session-x11 \
        power-profiles-daemon systemd-zram-generator gamemode steam-devices \
        mesa-vulkan-drivers vulkan-tools \
        systemd-oomd iw libmimalloc3 && \
    apt-get install -y --no-install-recommends \
        flatpak plasma-discover-backend-flatpak distrobox \
        slirp4netns uidmap fuse-overlayfs && \
    apt-get install -y --no-install-recommends \
        python3 \
        linux-modules-nvidia-615-open-generic \
        nvidia-kernel-common-615 libnvidia-gl-615 libnvidia-compute-615 \
        libnvidia-extra-615 libnvidia-cfg1-615 libnvidia-decode-615 \
        libnvidia-encode-615 libnvidia-fbc1-615 nvidia-utils-615 \
        nvidia-compute-utils-615 xserver-xorg-video-nvidia-615 && \
    apt-get purge -y plasma-discover-backend-snap snapd plasma-welcome && \
    rm -rf /var/lib/apt/lists/* /var/cache/* /var/log/*

# bootc-rootfs.sh deletes /var, so the payload carries no dpkg database: the
# KCM packages the Hub opens can only be asserted here, while it exists.
RUN set -eux; \
    for package in bluedevil kscreen powerdevil plasma-nm plasma-pa kde-config-flatpak kup-backup; do \
        test "$(dpkg-query -W -f='${db:Status-Status}' "$package")" = installed; \
    done; \
    for package in plasma-systemmonitor kamoso fprintd samba smbclient \
        kdenetwork-filesharing kaccounts-integration kio-gdrive krdp \
        mesa-vulkan-drivers vulkan-tools; do \
        test "$(dpkg-query -W -f='${db:Status-Status}' "$package")" = installed; \
    done; \
    test -s /usr/share/applications/kcm_kup.desktop; \
    test -s /usr/share/applications/kcm_app-permissions.desktop

# NVIDIA build gate. The image must carry exactly one kernel and the
# Canonical-signed nvidia modules built for it; a mismatch (the archive
# publishing a new kernel before its nvidia modules) fails the build here
# instead of shipping an image whose NVIDIA GPUs silently fall back to
# nouveau. depmod runs explicitly because the module package's own postinst
# cannot (no /boot/System.map in a layered build). The supported-device list
# is flattened from the driver's supported-gpus.json for rowyn-gpu-select.
# linux-modules-nvidia-* also ships an empty /lib/modules/kernel/nvidia-*
# tree; the gate drops it (only if empty) so exactly one kernel dir remains.
RUN --mount=type=bind,from=ctx,source=/,target=/ctx \
    set -eux; \
    if [ -d /usr/lib/modules/kernel ]; then \
        find /usr/lib/modules/kernel -depth -type d -empty -delete; \
        test ! -e /usr/lib/modules/kernel; \
    fi; \
    test "$(find /usr/lib/modules -mindepth 1 -maxdepth 1 -type d | wc -l)" = 1; \
    kver="$(basename "$(find /usr/lib/modules -mindepth 1 -maxdepth 1 -type d)")"; \
    for m in nvidia nvidia-modeset nvidia-drm nvidia-uvm; do \
        test -s "/usr/lib/modules/${kver}/kernel/nvidia-615-open/${m}.ko"; \
    done; \
    depmod -a "${kver}"; \
    modinfo -k "${kver}" nvidia | grep -E '^(filename|version):'; \
    test "$(modinfo -k "${kver}" -F version nvidia)" = "$(dpkg-query -W -f='${source:Upstream-Version}' nvidia-kernel-common-615)"; \
    modinfo -k "${kver}" -F signer nvidia | grep -q .; \
    python3 -I /ctx/shared/nvidia-open-devids.py \
        /usr/share/doc/nvidia-kernel-common-615/supported-gpus.json \
        /usr/lib/rowyn/nvidia-open-devids; \
    test "$(grep -vc '^#' /usr/lib/rowyn/nvidia-open-devids)" -gt 200; \
    rm -f /etc/dpkg/dpkg.cfg.d/rowyn-nvidia-gpus

# Locales. The installer offers whatever `localectl list-locales` reports
# on the live system (the live image generates only en_US.UTF-8, so the
# offered set is C.UTF-8 + en_US.UTF-8) and writes the choice to
# /etc/default/locale; a locale that was never generated silently falls
# back to C. `locales` (already a ubuntu-minimal dependency, named in the
# KDE layer) is generated for exactly that set: the archive lands in
# /usr/lib/locale, so it is part of the immutable image and survives the
# /var removal in bootc-rootfs.sh. locales-all (~236 MB) is deliberately not
# used. C.UTF-8 is built into libc-bin (/usr/lib/locale/C.utf8).
RUN printf 'en_US.UTF-8 UTF-8\n' > /etc/locale.gen && \
    locale-gen && \
    printf 'LANG=en_US.UTF-8\n' > /etc/default/locale && \
    locale -a | grep -qx en_US.utf8 && \
    locale -a | grep -qi ^c.utf && \
    test -e /usr/lib/locale/locale-archive -o -d /usr/lib/locale/en_US.utf8

# Canonical timezone state: the image ships exactly what
# systemd-timedated itself would write for UTC — an absolute
# /etc/localtime symlink into /usr/share/zoneinfo plus /etc/timezone —
# so the bootc /etc merge base is never a base-image default the
# installer's symlink swap or a later KDE/timedated change has to
# replace sight-unseen. tzdata, tzdata-legacy and polkitd are named
# explicitly in the layer above: tzdata guarantees the canonical zone
# files (America/Detroit included), tzdata-legacy the legacy aliases
# the zone tables still list (US/Michigan -> America/Detroit) — a
# zone the installer offers whose file is missing leaves a dangling
# /etc/localtime symlink and a clock silently stuck on UTC — and
# polkitd authorizes the KDE clock settings' timedated calls.
RUN printf 'Etc/UTC\n' > /etc/timezone && \
    ln -sf /usr/share/zoneinfo/Etc/UTC /etc/localtime

# SSH is installed so users can opt in, but release images do not enable it
# (openssh-server's postinst may enable ssh.socket, so disable both). The
# drop-in is always installed so an opted-in sshd is key-only and root-less.
RUN systemctl enable systemd-resolved && \
    systemctl disable ssh.service ssh.socket && \
    install -d -m 0755 /etc/ssh/sshd_config.d && \
    printf 'PasswordAuthentication no\nKbdInteractiveAuthentication no\nPermitRootLogin no\n' \
        > /etc/ssh/sshd_config.d/10-rowyn.conf && \
    chmod 0644 /etc/ssh/sshd_config.d/10-rowyn.conf && \
    mkdir -p /etc/netplan && \
    printf 'network:\n  version: 2\n  renderer: NetworkManager\n' > /etc/netplan/90-default.yaml && \
    chmod 0600 /etc/netplan/90-default.yaml

# Boot splash: the Rowyn Plymouth theme (pulsing berry mark on deep navy)
# must be installed before the initramfs is generated — dracut's plymouth
# module folds the theme named by the default.plymouth update-alternative
# into the initramfs, and initramfs.sh fails the build if the theme is
# not inside it.
COPY docs/assets/rowyn-logo.png /usr/share/plymouth/themes/rowyn/rowyn-logo.png
RUN --mount=type=bind,from=ctx,source=/,target=/ctx \
    /ctx/shared/plymouth-theme.sh --root /

RUN --mount=type=tmpfs,dst=/tmp --mount=type=tmpfs,dst=/root \
    --mount=type=bind,from=ctx,source=/,target=/ctx \
    /ctx/shared/initramfs.sh

# Keep service identities declarative across locally merged /etc upgrades.
# Journald is the default logging engine; do not start a redundant rsyslog.
RUN --mount=type=bind,from=ctx,source=/,target=/ctx \
    /ctx/shared/service-accounts-and-logging.sh --root / && \
    echo "HOME=/var/home" | tee -a "/etc/default/useradd" && \
    /ctx/shared/bootc-rootfs.sh

# Keep the update verifier and its public key in the immutable payload. The
# policy is generated after stateless normalization so it never depends on
# /var and is available to bootc update fetches from first boot onward.
COPY keys/update.pub /usr/share/rowyn/update.pub
RUN --mount=type=bind,from=ctx,source=/,target=/ctx \
    install -Dm755 /ctx/shared/update-policy.sh /usr/libexec/rowyn-update-policy && \
    /usr/libexec/rowyn-update-policy --root /

# Per-user counterpart to the strict system policy above: seed a
# permissive containers policy through /etc/skel so new accounts get a
# ~/.config/containers/policy.json that lets rootless podman/distrobox
# pull from any registry. Root (and therefore bootc) never reads skel,
# so update verification keeps using the fail-closed system policy.
RUN --mount=type=bind,from=ctx,source=/,target=/ctx \
    /ctx/shared/containers-user-policy.sh --root /

# First-boot policy must be generated after stateless normalization. It keeps
# mutable SDDM/resolver overrides in /etc and masks only the unused Budgie
# background cache activation paths.
RUN --mount=type=bind,from=ctx,source=/,target=/ctx \
    /ctx/shared/installed-desktop-defaults.sh --root / && \
    /ctx/shared/installed-apps.sh --root /

# The rootfs normalizer deletes /var. Keep KDE defaults in /etc so they
# survive stateless first boot.
RUN --mount=type=bind,from=ctx,source=/,target=/ctx \
    /ctx/shared/kde-dark-defaults.sh --root / && \
    /ctx/shared/kde-wallpaper-defaults.sh --root / && \
    /ctx/shared/browser-defaults.sh --root /

# Brave is the default browser, shipped the way Flatpak intends OS
# vendors to ship apps: a .preinstall declaration in the immutable tree
# plus a boot-time oneshot that runs `flatpak preinstall` against the
# system installation (the static Flathub remote from installed-apps.sh
# serves it). Nothing is baked into /var; preinstall tracks the install
# (and a user's later uninstall) in the system installation itself.
RUN --mount=type=bind,from=ctx,source=/,target=/ctx \
    install -Dm644 /ctx/shared/brave.preinstall /usr/share/flatpak/preinstall.d/rowyn-brave.preinstall && \
    install -Dm644 /ctx/shared/rowyn-flatpak-preinstall.service /usr/lib/systemd/system/rowyn-flatpak-preinstall.service && \
    systemctl enable rowyn-flatpak-preinstall.service

# Performance defaults (zram, sysctl, MGLRU/THP, ntsync, gaming limits) are
# vendor-dir files under /usr/lib, so /etc overrides win.
RUN --mount=type=bind,from=ctx,source=/,target=/ctx \
    /ctx/shared/performance-defaults.sh --root /

# Desktop tuning: I/O schedulers, journald caps, Baloo off, PipeWire
# low-latency quantum and the performance power profile applied only while
# GameMode is active, systemd-oomd thresholds, sleep policy, slice weights,
# PowerDevil profile defaults, and the timezone-derived Wi-Fi regulatory
# domain (rowyn-wifi-regdom, which needs the iw package above; systemd-oomd
# is not pulled in by any KDE package, so it is named explicitly and enabled).
RUN --mount=type=bind,from=ctx,source=/,target=/ctx \
    /ctx/shared/desktop-tuning.sh --root / && \
    systemctl enable rowyn-wifi-regdom.service systemd-oomd.service systemd-oomd.socket

# Opt-in gaming helpers: gaming slice + rowyn-game-launch, rowyn-mimalloc-run,
# and rowyn-tune (aggressive latency sysctls, off by default).
RUN --mount=type=bind,from=ctx,source=/,target=/ctx \
    /ctx/shared/gaming-defaults.sh --root /

# GPU driver selection. One image serves every machine: the nvidia open
# modules and nouveau are both installed, neither is in the initramfs, and
# rowyn-gpu-select (sysinit, before udev coldplug) writes /run/modprobe.d
# so a supported NVIDIA GPU gets nvidia and an older one gets nouveau.
# nvidia-defaults.sh ships the static module options, removes the packages'
# static nouveau blacklist, and gates the NVIDIA sleep units on the driver
# actually being loaded; the sleep units themselves are enabled by the
# packages' postinst and re-enabled here so the build does not depend on it.
RUN --mount=type=bind,from=ctx,source=/,target=/ctx \
    /ctx/shared/nvidia-defaults.sh --root / && \
    install -Dm755 /ctx/shared/gpu-select.sh /usr/libexec/rowyn-gpu-select && \
    install -Dm644 /ctx/shared/rowyn-gpu-select.service /usr/lib/systemd/system/rowyn-gpu-select.service && \
    systemctl enable rowyn-gpu-select.service nvidia-suspend.service \
        nvidia-resume.service nvidia-hibernate.service

# --- Rowyn Hub ---------------------------------------------------------------
# System hub (Dashboard, Apps, Updates, VPN) compiled by the hub-builder
# stage from the pinned, ported upstream source. Runtime libraries for the
# Tauri shell and the VPN stack (openconnect, vpnc-scripts, ksshaskpass,
# firewalld) are in the KDE package layer above.
COPY --from=hub-builder /rowyn-hub-shell /usr/bin/rowyn-hub-shell
COPY shared/com.rowyn.hub.desktop /usr/share/applications/com.rowyn.hub.desktop
COPY build/hub/src-tauri/data/kde/servicemenus/rowyn-development-here.desktop /usr/share/kio/servicemenus/rowyn-development-here.desktop
COPY docs/assets/rowyn-logo.png /usr/share/icons/hicolor/512x512/apps/com.rowyn.hub.png

# The VPN backend pins the Fedora paths /usr/bin/openconnect and
# --script /etc/vpnc/vpnc-script (its own regression tests assert them);
# Ubuntu ships the binary in /usr/sbin and the script in
# /usr/share/vpnc-scripts. Bridge the layout with symlinks instead of
# forking the pinned argv construction.
RUN ln -s /usr/sbin/openconnect /usr/bin/openconnect && \
    mkdir -p /etc/vpnc && \
    ln -s /usr/share/vpnc-scripts/vpnc-script /etc/vpnc/vpnc-script

# --- Hub update helpers -------------------------------------------------------
# The Hub's Updates actions shell out to fixed helper binaries: the check
# and status reads go through the bootc guard (`sudo -n`, so the sudoers
# allowlist below is what lets them run without a password), staging goes
# through the safe-upgrade helper (`sudo -A`), and applying a staged
# update reboots through the finalizer. Rowyn's implementations are the
# shell scripts in shared/ (fixed-operation counterparts of the upstream Rust
# helpers); the sudoers file grants exactly their fixed argv to %sudo.
RUN --mount=type=bind,from=ctx,source=/,target=/ctx \
    install -Dm755 /ctx/shared/rowyn-safe-upgrade.sh /usr/bin/rowyn-safe-upgrade && \
    install -Dm755 /ctx/shared/rowyn-bootc-guard.sh /usr/bin/rowyn-bootc-guard && \
    install -Dm755 /ctx/shared/rowyn-finalize-staged.sh /usr/libexec/rowyn-finalize-staged && \
    install -Dm440 /ctx/shared/rowyn-upgrade.sudoers /etc/sudoers.d/rowyn-upgrade && \
    visudo -cf /etc/sudoers.d/rowyn-upgrade

# Support tools: local-only scrubbed bug report and controller readiness check
# (python3 standard library only; both are read-only and make no network calls).
RUN --mount=type=bind,from=ctx,source=/,target=/ctx \
    install -Dm755 /ctx/shared/rowyn-report /usr/bin/rowyn-report && \
    install -Dm755 /ctx/shared/rowyn-controller-check /usr/bin/rowyn-controller-check && \
    for t in rowyn-report rowyn-controller-check; do \
      python3 -c "import py_compile,sys; py_compile.compile(sys.argv[1], cfile=sys.argv[2], doraise=True)" /usr/bin/$t /tmp/$t.pyc && \
      rm -f /tmp/$t.pyc; \
    done

# Automatic background staging (policy-gated; applies on next reboot).
RUN --mount=type=bind,from=ctx,source=/,target=/ctx \
    install -Dm755 /ctx/shared/rowyn-update-watcher /usr/libexec/rowyn-update-watcher && \
    install -Dm755 /ctx/shared/rowyn-update-watcher-run /usr/libexec/rowyn-update-watcher-run && \
    install -Dm755 /ctx/shared/rowyn-update-watcher-status /usr/libexec/rowyn-update-watcher-status && \
    python3 -c "import py_compile; py_compile.compile('/usr/libexec/rowyn-update-watcher-status', cfile='/tmp/rowyn-update-watcher-status.pyc', doraise=True)" && rm -f /tmp/rowyn-update-watcher-status.pyc && \
    install -Dm644 /ctx/shared/update-policy.conf /usr/share/rowyn/update-policy.conf && \
    install -Dm644 /ctx/shared/rowyn-update-watcher.service /usr/lib/systemd/system/rowyn-update-watcher.service && \
    install -Dm644 /ctx/shared/rowyn-update-watcher.timer /usr/lib/systemd/system/rowyn-update-watcher.timer && \
    systemctl enable rowyn-update-watcher.timer

# Passive per-user notification, deduplicated by the exact staged image digest.
# No automatic reboot and no privilege prompts in the desktop session.
RUN --mount=type=bind,from=ctx,source=/,target=/ctx \
    install -Dm755 /ctx/shared/rowyn-update-notify /usr/bin/rowyn-update-notify && \
    install -Dm644 /ctx/shared/rowyn-update-notify.service /usr/lib/systemd/user/rowyn-update-notify.service && \
    install -Dm644 /ctx/shared/rowyn-update-notify.timer /usr/lib/systemd/user/rowyn-update-notify.timer && \
    systemctl --global enable rowyn-update-notify.timer

# Boot health: a deployment that has never been confirmed healthy runs bounded
# local checks once per boot; three failures quarantine the digest and roll the
# machine back. Rowyn's own systemd + bootc implementation, not greenboot.
RUN --mount=type=bind,from=ctx,source=/,target=/ctx \
    install -Dm755 /ctx/shared/rowyn-boot-health /usr/libexec/rowyn-boot-health && \
    python3 -c "import py_compile,sys; py_compile.compile(sys.argv[1], cfile=sys.argv[2], doraise=True)" /usr/libexec/rowyn-boot-health /tmp/rowyn-boot-health.pyc && \
    rm -f /tmp/rowyn-boot-health.pyc && \
    install -Dm644 /ctx/shared/rowyn-boot-health.service /usr/lib/systemd/system/rowyn-boot-health.service && \
    install -Dm644 /ctx/shared/rowyn-boot-health.timer /usr/lib/systemd/system/rowyn-boot-health.timer && \
    systemctl enable rowyn-boot-health.timer

# --- Rowyn identity ----------------------------------------------------------
COPY os-release /usr/lib/os-release
RUN --mount=type=bind,from=ctx,source=/,target=/ctx \
    python3 /ctx/shared/write-build-info.py --root / \
      --source-revision "${ROWYN_SOURCE_REVISION}" --image-version "${ROWYN_IMAGE_VERSION}"

# No login users are baked into the image. The KDE first-login defaults a
# new account needs are already system-wide: kde-dark-defaults.sh --root /
# above installs them into /etc/xdg (applies to every account) and
# /etc/skel (the installer copies /etc/skel into the account it creates),
# so the removed spike user's per-user tmpfiles bake has no equivalent
# here — there is deliberately no per-user seeding for an account that
# must not exist.

# Systems installed from earlier images keep the removed spike account
# (password rowyn, passwordless sudo, SSH key): bootc's 3-way /etc merge
# preserves locally modified passwd/shadow/group. This one-shot unit locks
# that account, drops it from sudo and removes its sudoers file and key.
# It matches the account only by the retired spike SSH key
# (shared/retired-dev-key.pub), never by name,
# so a real user named rowyn is left alone.
RUN --mount=type=bind,from=ctx,source=/,target=/ctx \
    install -Dm755 /ctx/shared/retire-spike-account.sh /usr/libexec/rowyn-retire-spike-account && \
    install -Dm644 /ctx/shared/print-test-page.txt /usr/share/rowyn/print-test-page.txt && \
    install -Dm644 /ctx/shared/retired-dev-key.pub /usr/share/rowyn/retired-dev-key.pub && \
    install -Dm644 /ctx/shared/rowyn-retire-spike-account.service /usr/lib/systemd/system/rowyn-retire-spike-account.service && \
    systemctl enable rowyn-retire-spike-account.service

# Desktop enablement:
# - NetworkManager owns networking on the desktop (plasma-nm expects it):
#   a wildcard wired profile + an explicit "manage everything" drop-in,
#   and systemd-networkd masked so it cannot race NM for the interface.
# - firewalld is the only firewall manager: ubuntu-standard recommends
#   ufw, so ufw.service is masked (the networkd treatment) rather than
#   letting two nftables managers race — the Hub's VPN lockdown drives
#   firewall-cmd.
RUN mkdir -p /etc/NetworkManager/system-connections /etc/NetworkManager/conf.d && \
    printf '[connection]\nid=rowyn-wired\ntype=ethernet\nautoconnect=true\n\n[ethernet]\n\n[ipv4]\nmethod=auto\n\n[ipv6]\nmethod=auto\n' \
        > /etc/NetworkManager/system-connections/rowyn-wired.nmconnection && \
    chmod 0600 /etc/NetworkManager/system-connections/rowyn-wired.nmconnection && \
    printf '[keyfile]\nunmanaged-devices=none\n' > /etc/NetworkManager/conf.d/99-rowyn-managed.conf && \
    systemctl enable sddm.service NetworkManager.service firewalld.service \
        cups.service cups.socket cups-browsed.service \
        avahi-daemon.service avahi-daemon.socket power-profiles-daemon.service && \
    systemctl set-default graphical.target && \
    systemctl disable NetworkManager-wait-online.service && \
    systemctl mask systemd-networkd.service systemd-networkd-wait-online.service ufw.service && \
    netplan generate --root-dir /

# Keep host package guidance out of the Ubuntu live installer and install it
# only after all image-build APT transactions and stateless normalization.
RUN --mount=type=bind,from=ctx,source=/,target=/ctx \
    /ctx/shared/host-package-guidance.sh --root /

LABEL containers.bootc 1

RUN bootc container lint
