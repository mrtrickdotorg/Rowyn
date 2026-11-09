#!/usr/bin/env bash
# Install Rowyn's opt-in gaming helpers: a high-priority user slice and launch
# wrapper, a mimalloc wrapper, and `rowyn-tune` for the aggressive gaming
# sysctl bundle (off by default; it trades disk-write throughput for latency).
#
# Vendor locations only (/usr/lib, /usr/libexec, /usr/bin); the one /etc file
# is written at run time by `rowyn-tune on`. Idempotent: each file is
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
[[ "$root" == / ]] || root="${root%/}"

write_file() {
    local path
    path=$(printf '%s/%s' "${root%/}" "${1#/}")
    install -d "$(dirname "$path")"
    cat > "$path"
    chmod "$2" "$path"
}

# User slice: games launched through rowyn-game-launch get more CPU and I/O
# weight than the default user slice. No MemoryMax: a cap kills games that
# briefly spike instead of helping them.
write_file /usr/lib/systemd/user/rowyn-gaming.slice 0644 <<'EOF2'
# Rowyn gaming slice: used by rowyn-game-launch.
[Unit]
Description=Rowyn gaming slice

[Slice]
CPUWeight=300
IOWeight=200
EOF2

# Steam: set the launch options to `rowyn-game-launch %command%`.
write_file /usr/bin/rowyn-game-launch 0755 <<'EOF2'
#!/bin/sh
# Run a command in the Rowyn gaming slice, under GameMode when available.
# Falls back to a plain run if the user systemd manager cannot be reached.
# The command is not exec'd: the wrapper waits so it can record the
# session (game, start, duration) in the local telemetry database the
# Hub's session charts read. Recording is best-effort — a recorder
# failure never changes the game's exit status, which is preserved.
[ "$#" -gt 0 ] || { echo "usage: rowyn-game-launch COMMAND [ARGS...]" >&2; exit 2; }
game=$(basename "$1")
started=$(date +%s)
if [ -x /usr/games/gamemoderun ]; then
    set -- /usr/games/gamemoderun "$@"
fi
if command -v systemd-run >/dev/null 2>&1 && systemctl --user is-system-running >/dev/null 2>&1; then
    systemd-run --user --scope --quiet --slice=rowyn-gaming.slice -- "$@"
else
    "$@"
fi
status=$?
if [ -x /usr/bin/rowyn-telemetry-record ]; then
    /usr/bin/rowyn-telemetry-record "$game" "$started" "$(( $(date +%s) - started ))" >/dev/null 2>&1 || true
fi
exit $status
EOF2

# Per-game opt-in: never preloaded globally.
write_file /usr/bin/rowyn-mimalloc-run 0755 <<'EOF2'
#!/bin/sh
# Run one command with the mimalloc allocator (some engines see fewer
# allocation stalls). 32-bit processes may log a preload warning; harmless.
[ "$#" -gt 0 ] || { echo "usage: rowyn-mimalloc-run COMMAND [ARGS...]" >&2; exit 2; }
LD_PRELOAD="libmimalloc.so.3${LD_PRELOAD:+:$LD_PRELOAD}"
export LD_PRELOAD
MIMALLOC_LARGE_OS_PAGES=1
export MIMALLOC_LARGE_OS_PAGES
exec "$@"
EOF2

write_file /usr/bin/rowyn-tune 0755 <<'EOF2'
#!/bin/bash
# rowyn-tune: switch Rowyn's opt-in latency profile.
#   rowyn-tune status | on | off
# "on" writes /etc/sysctl.d/90-rowyn-gaming.conf: small dirty-page limits and
# no proactive compaction, which cut write-back and compaction stalls in games
# but lower sustained disk-write throughput (builds, large copies).
set -euo pipefail
conf=${ROWYN_TUNE_CONF:-/etc/sysctl.d/90-rowyn-gaming.conf}
case "${1:-status}" in
    status)
        if [ -e "$conf" ]; then echo gaming; else echo default; fi
        ;;
    on|off)
        if [ "$(id -u)" -ne 0 ] && [ -z "${ROWYN_TUNE_CONF:-}" ]; then
            exec sudo -A "$0" "$1"
        fi
        if [ "$1" = on ]; then
            install -d "$(dirname "$conf")"
            cat > "$conf" <<'EOF3'
# Written by `rowyn-tune on`; `rowyn-tune off` removes it.
vm.dirty_ratio = 5
vm.dirty_background_ratio = 5
vm.dirty_expire_centisecs = 100
vm.dirty_writeback_centisecs = 500
vm.compaction_proactiveness = 0
net.core.busy_poll = 50
net.core.busy_read = 50
EOF3
            chmod 0644 "$conf"
            [ -n "${ROWYN_TUNE_CONF:-}" ] || sysctl -q -p "$conf" || true
        else
            rm -f "$conf"
            echo "Defaults return at the next boot." >&2
        fi
        ;;
    *)
        echo "usage: rowyn-tune [status|on|off]" >&2
        exit 2
        ;;
esac
EOF2
