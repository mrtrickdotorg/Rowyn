#!/usr/bin/env bash
# Install Rowyn's small desktop tuning defaults: per-device I/O schedulers,
# journald size caps, Baloo filename-only indexing, low-latency PipeWire and the
# performance power profile applied only while a game runs under GameMode,
# systemd-oomd thresholds, sleep policy, user/system slice weights, PowerDevil
# AC/battery power-profile defaults and the Wi-Fi regulatory domain service.
#
# Everything lives in vendor locations (/usr/lib, /etc/xdg) so users and
# administrators can override it. /etc/gamemode.ini is the one exception: it
# is GameMode's only system-wide override location (the package's own
# defaults in /usr/share/gamemode/gamemode.ini are loaded first and merged,
# so this file carries only the [custom] hooks). Idempotent: each file is
# rewritten in full.
set -euo pipefail

root=/
while (($#)); do
    case "$1" in
        --root)
            root=${2:?--root requires a path}
            shift 2
            ;;
        *)
            echo "usage: $0 [--root PATH]" >&2
            exit 2
            ;;
    esac
done

if [[ "$root" != / ]]; then
    root="${root%/}"
fi
root_path() {
    if [[ "$1" == /* ]]; then
        printf '%s%s' "$root" "$1"
    else
        printf '%s/%s' "$root" "$1"
    fi
}

# write_file PATH MODE: stdin becomes the file.
write_file() {
    local path
    path=$(root_path "$1")
    install -d "$(dirname "$path")"
    cat > "$path"
    chmod "$2" "$path"
}

# Ubuntu's udev/systemd ship no I/O scheduler rules. Only sd* whole disks
# (SATA/SAS/USB) are matched, so nvme (kernel default "none"), virtio (vd*),
# loop, ram, zram, dm and mmc devices are never touched.
write_file /usr/lib/udev/rules.d/60-rowyn-ioschedulers.rules 0644 <<'EOF2'
# Rowyn I/O scheduler defaults by device type. Override with an
# /etc/udev/rules.d/60-rowyn-ioschedulers.rules of the same name.
ACTION=="remove", GOTO="rowyn_ioscheduler_end"
SUBSYSTEM!="block", GOTO="rowyn_ioscheduler_end"
ENV{DEVTYPE}!="disk", GOTO="rowyn_ioscheduler_end"
# Only SCSI-layer disks (SATA/SAS/USB); never nvme, vd, loop, ram, zram, dm.
KERNEL!="sd[a-z]|sd[a-z][a-z]", GOTO="rowyn_ioscheduler_end"

# Rotational disks: bfq keeps the desktop responsive under heavy seeking.
ATTR{queue/rotational}=="1", ATTR{queue/scheduler}="bfq"
# SATA/SAS SSDs: mq-deadline has low overhead and no seek logic to waste.
ATTR{queue/rotational}=="0", ATTR{queue/scheduler}="mq-deadline"

LABEL="rowyn_ioscheduler_end"
EOF2

write_file /usr/lib/systemd/journald.conf.d/90-rowyn.conf 0644 <<'EOF2'
# Rowyn journal size caps. Override in /etc/systemd/journald.conf.d/.
[Journal]
SystemMaxUse=200M
SystemMaxFileSize=32M
RuntimeMaxUse=64M
MaxRetentionSec=2week
# Nothing reads a syslog socket on this image; skip the extra copy.
ForwardToSyslog=no
EOF2

# KDE reads /etc/xdg through XDG_CONFIG_DIRS; a user's ~/.config/baloofilerc
# overrides this, so a user can change any of it in System Settings.
# Filename (basic) indexing only: the content extractor is the expensive
# half of Baloo (it opens and parses every document), while filename
# search in Dolphin/KRunner is what users actually feel. KMail search is
# Akonadi's own index and does not depend on Baloo content extraction.
# Baloo indexes $HOME and skips hidden trees on its own; the excludes
# name the non-hidden trees that are pure churn (a leftover snap dir,
# VM image folders, container storage).
write_file /etc/xdg/baloofilerc 0644 <<'EOF2'
# Rowyn: filename indexing on, content indexing off. Change in
# System Settings > Search > File Search.
[Basic Settings]
Indexing-Enabled=true

[General]
only basic indexing=true
exclude folders[$e]=$HOME/snap,$HOME/VirtualBox VMs,$HOME/VMs,$HOME/.local/share/containers
EOF2

write_file /usr/libexec/rowyn-gamemode-audio 0755 <<'EOF2'
#!/bin/sh
# Called by GameMode (start/end hooks). GameMode runs as a systemd user
# service, so this executes inside the game user's session and pw-metadata
# reaches that user's PipeWire. The default quantum is kept for normal use
# (Bluetooth needs it); 256 samples at 48 kHz is about 5.3 ms while gaming.
# Always exits 0: audio tuning must never disturb a game launch.
case "${1:-}" in
    start) quantum=256 ;;
    end) quantum=0 ;;
    *) exit 0 ;;
esac
command -v pw-metadata >/dev/null 2>&1 || exit 0
if command -v timeout >/dev/null 2>&1; then
    timeout 5 pw-metadata -n settings 0 clock.force-quantum "$quantum" >/dev/null 2>&1 || true
else
    pw-metadata -n settings 0 clock.force-quantum "$quantum" >/dev/null 2>&1 || true
fi
exit 0
EOF2

# Power profile while gaming. PowerDevil (below) switches power-profiles-daemon
# between AC and battery, but never to "performance"; this hook raises it only
# for the duration of a GameMode session and restores what was active before.
# The saved profile lives in $XDG_RUNTIME_DIR (the game user's tmpfs).
write_file /usr/libexec/rowyn-gamemode-power 0755 <<'EOF2'
#!/bin/sh
# Called by GameMode (start/end hooks) inside the game user's session.
# start: remember the active power-profiles-daemon profile, switch to
#        "performance" if this machine offers it (some laptops expose only
#        balanced/power-saver: then nothing changes).
# end:   restore the remembered profile, unless the user changed it meanwhile.
# Always exits 0: power tuning must never disturb a game launch.
command -v powerprofilesctl >/dev/null 2>&1 || exit 0
[ -n "${XDG_RUNTIME_DIR:-}" ] && [ -d "$XDG_RUNTIME_DIR" ] || exit 0
state="$XDG_RUNTIME_DIR/rowyn-gamemode-power"

ppc() {
    if command -v timeout >/dev/null 2>&1; then
        timeout 5 powerprofilesctl "$@"
    else
        powerprofilesctl "$@"
    fi
}

case "${1:-}" in
    start)
        # GameMode runs start once per session, but never overwrite a saved
        # profile: that would lose the user's original choice.
        [ -e "$state" ] && exit 0
        ppc list 2>/dev/null | grep -Eq '^[ *]+performance:' || exit 0
        current=$(ppc get 2>/dev/null) || exit 0
        case "$current" in
            '' | performance | *[!a-z-]*) exit 0 ;;
        esac
        printf '%s\n' "$current" > "$state" 2>/dev/null || exit 0
        ppc set performance >/dev/null 2>&1 || rm -f "$state"
        ;;
    end)
        [ -r "$state" ] || exit 0
        previous=$(sed -n '1p' "$state" 2>/dev/null)
        rm -f "$state"
        case "$previous" in
            '' | *[!a-z-]*) exit 0 ;;
        esac
        # Only undo our own change: leave a profile the user picked in-game.
        [ "$(ppc get 2>/dev/null)" = performance ] || exit 0
        ppc set "$previous" >/dev/null 2>&1 || true
        ;;
esac
exit 0
EOF2

# GameMode loads /usr/share/gamemode/gamemode.ini, then /etc/gamemode.ini,
# merging per key (repeated start=/end= lines in [custom] are appended, run in
# order), so only the keys Rowyn changes are needed here.
write_file /etc/gamemode.ini 0644 <<'EOF2'
# Rowyn: low-latency PipeWire and the performance power profile only while a
# game runs under GameMode. The package defaults in
# /usr/share/gamemode/gamemode.ini still apply.
[general]
# Raise the game's priority (nice -10) while GameMode is active. GameMode
# skips this quietly if the user lacks RLIMIT_NICE headroom.
renice=10

[custom]
start=/usr/libexec/rowyn-gamemode-audio start
end=/usr/libexec/rowyn-gamemode-audio end
start=/usr/libexec/rowyn-gamemode-power start
end=/usr/libexec/rowyn-gamemode-power end
EOF2

# systemd-oomd. Ubuntu's systemd-oomd package defaults (kill user@.service at
# 50% memory pressure for 20 s) fire during game loading screens (shader
# compilation and asset streaming sustain high pressure for 20-40 s) and kill
# browsers and launchers. Raise the limit to 65% for 40 s and let swap use go
# to 85% (zram compresses about 3:1). user@.service carries its own explicit
# 50% in the package's 10-oomd-user-service-defaults.conf, which the global
# default would not override, so it is raised the same way here.
write_file /usr/lib/systemd/oomd.conf.d/90-rowyn.conf 0644 <<'EOF2'
# Rowyn systemd-oomd thresholds. Override in /etc/systemd/oomd.conf.d/.
[OOM]
SwapUsedLimit=85%
DefaultMemoryPressureLimit=65%
DefaultMemoryPressureDurationSec=40s
EOF2

write_file /usr/lib/systemd/system/user.slice.d/90-rowyn-oomd.conf 0644 <<'EOF2'
# Rowyn: monitor all user sessions; oomd kills the worst cgroup in
# user.slice before the kernel OOM killer picks something session-critical.
[Slice]
ManagedOOMSwap=kill
ManagedOOMMemoryPressure=kill
ManagedOOMMemoryPressureLimit=65%
EOF2

write_file /usr/lib/systemd/system/user@.service.d/90-rowyn-oomd.conf 0644 <<'EOF2'
# Rowyn: the package default for the per-user manager is 50%; keep it in step
# with the user.slice limit so game loading is not killed.
[Service]
ManagedOOMMemoryPressure=kill
ManagedOOMMemoryPressureLimit=65%
EOF2

# Sleep policy. Hybrid sleep and suspend-then-hibernate wake to a black screen
# on NVMe + NVIDIA machines, and Rowyn provisions no resume= swap target, so
# hibernation is off. SuspendState tries each entry in order and uses the
# first the kernel accepts, so S3 machines stay on "mem" while s2idle-only
# laptops fall through to "freeze". Re-enable in /etc/systemd/sleep.conf.d/.
write_file /usr/lib/systemd/sleep.conf.d/90-rowyn.conf 0644 <<'EOF2'
# Rowyn sleep policy: suspend only. Override in /etc/systemd/sleep.conf.d/.
[Sleep]
AllowHibernation=no
AllowHybridSleep=no
AllowSuspendThenHibernate=no
SuspendState=mem standby freeze
EOF2

# Background batch work must never compete with the foreground: the two
# periodic re-indexers a desktop carries (plocate's database rebuild and
# man-db's cache regeneration) run at idle I/O priority and a quarter of
# the default CPU/IO weight. The timers stay — locate and man work —
# they just yield. Drop-ins for a unit a given image lacks are ignored
# by systemd, so these are safe whether or not the package is present.
write_file /usr/lib/systemd/system/plocate-updatedb.service.d/90-rowyn.conf 0644 <<'EOF2'
# Rowyn: the locate database rebuild yields to foreground work.
[Service]
Nice=10
IOSchedulingClass=idle
CPUWeight=25
IOWeight=25
EOF2

write_file /usr/lib/systemd/system/man-db.service.d/90-rowyn.conf 0644 <<'EOF2'
# Rowyn: man-db cache regeneration yields to foreground work.
[Service]
Nice=10
IOSchedulingClass=idle
CPUWeight=25
IOWeight=25
EOF2

# Slice weights: interactive desktop work beats background system services
# (system.slice keeps systemd's default weight of 100).
write_file /usr/lib/systemd/system/user.slice.d/90-rowyn-weights.conf 0644 <<'EOF2'
# Rowyn: favour the desktop session over system services under contention.
[Slice]
CPUWeight=1000
IOWeight=1000
EOF2

# PowerDevil only drives power-profiles-daemon when a profile has an explicit
# PowerProfile value (the shipped default is empty, so out of the box the
# profile never follows AC/battery). Group/key names are from
# PowerDevilProfileSettings.kcfg: [<AC|Battery|LowBattery>][Performance].
# A user's ~/.config/powerdevilrc (System Settings > Power) overrides these.
write_file /etc/xdg/powerdevilrc 0644 <<'EOF2'
# Rowyn: balanced on AC, power-saver on battery and low battery.
[AC][Performance]
PowerProfile=balanced

[Battery][Performance]
PowerProfile=power-saver

[LowBattery][Performance]
PowerProfile=power-saver
EOF2

# BlueDevil's KConfigXT launchState controls its initial-state restoration.
# Make Enable immutable so an old per-user Remember setting (and saved
# adapter _powered=false entries) cannot override Rowyn's startup policy.
# This affects only launch; users can still disable Bluetooth for the session.
write_file /etc/xdg/bluedevilglobalrc 0644 <<'EOF2'
# Rowyn: BlueDevil enables Bluetooth at desktop launch; session toggles remain usable.
[General]
launchState[$i]=enable
EOF2

# Wi-Fi regulatory domain from the timezone (script and unit live beside this
# helper; the unit is enabled by the Containerfile).
src_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
install -D -m 0755 "$src_dir/rowyn-wifi-regdom" "$(root_path /usr/libexec/rowyn-wifi-regdom)"
install -D -m 0644 "$src_dir/rowyn-wifi-regdom.service" \
    "$(root_path /usr/lib/systemd/system/rowyn-wifi-regdom.service)"
