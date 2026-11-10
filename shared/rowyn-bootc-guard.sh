#!/usr/bin/env bash
# Rowyn passwordless bootc gateway for the Hub.
#
# A fixed-operation wrapper in the shape of the upstream bootc guard: the
# Hub's unprivileged shell reaches bootc's root-only sysroot reads through
# `sudo -n` (allowlisted in /etc/sudoers.d/rowyn-upgrade), and every
# operation is a fixed argv — no caller-provided image reference or extra
# arguments are possible. Rowyn publishes two channels of the same image:
# stable (switch-latest) and testing (switch-testing), both promoted from
# the same verified digest by the image pipeline; the upstream cachy
# channels do not exist here and are refused rather than mapped onto
# something they are not.
#
# Operations (exactly one argument; anything else is a usage error):
#   status        bootc status (human readable), 30s bound
#   status-json   bootc status --json, 30s bound
#   check         bootc upgrade --check, 90s bound; stdout/stderr pass
#                 through untouched — the Hub parses bootc's own
#                 "Update available for:" / "No changes in:" wording
#   switch-latest bootc switch ghcr.io/mrtrickdotorg/rowyn:stable from any
#                 other deployment, or bootc upgrade once already on it,
#                 under the bootc flock, then finalize the staged deployment
#   switch-testing
#                 the same, targeting ghcr.io/mrtrickdotorg/rowyn:testing
set -uo pipefail

readonly LOCK_FILE=/run/rowyn-bootc.lock
readonly STABLE_REF=ghcr.io/mrtrickdotorg/rowyn:stable
readonly TESTING_REF=ghcr.io/mrtrickdotorg/rowyn:testing

booted_image_reference() {
    local status_json ref
    status_json=$(timeout 30 /usr/bin/bootc status --json) || return 1
    ref=$(printf '%s' "$status_json" | /usr/bin/python3 -c '
import json, re, sys
try:
    doc = json.load(sys.stdin)
    ref = doc["status"]["booted"]["image"]["image"]["image"]
    if not isinstance(ref, str) or not re.fullmatch(
        r"ghcr\.io/(?:kyth-os/rowyn|mrtrickdotorg/rowyn)(?::[A-Za-z0-9_.-]+|@sha256:[0-9a-f]{64})", ref
    ):
        raise ValueError("unsupported image reference")
    print(ref)
except (KeyError, TypeError, ValueError, json.JSONDecodeError):
    sys.exit(1)
') || return 1
    printf '%s' "$ref"
}

if (( $# != 1 )); then
    echo "usage: rowyn-bootc-guard <status|status-json|check|switch-latest|switch-testing>" >&2
    exit 2
fi

case "$1" in
    status)
        exec timeout 30 /usr/bin/bootc status
        ;;
    status-json)
        exec timeout 30 /usr/bin/bootc status --json
        ;;
    check)
        exec timeout 90 /usr/bin/bootc upgrade --check
        ;;
    switch-latest)
        TARGET_REF=$STABLE_REF
        ;;
    switch-testing)
        TARGET_REF=$TESTING_REF
        ;;
    switch-latest-cachy|switch-testing-cachy)
        echo "rowyn-bootc-guard: channel '$1' does not exist on Rowyn; the published channels are stable and testing" >&2
        exit 2
        ;;
    *)
        echo "rowyn-bootc-guard: unsupported operation: $1" >&2
        exit 2
        ;;
esac

# switch-*: serialize against rowyn-safe-upgrade (and any other
# holder of the same flock) before touching deployments. A busy lock is a
# retryable verdict, reported before any mount or deployment changes.
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    echo "Another bootc upgrade is in progress; will retry on the next run" >&2
    exit 1
fi

# The upstream guard prepares /boot (remount read-write) before a switch
# because its
# /boot is a read-only bind; Rowyn's bootc manages its boot partition
# itself, so preparation is best-effort here and never aborts the switch.
if [[ -x /usr/libexec/rowyn-finalize-staged ]]; then
    /usr/libexec/rowyn-finalize-staged prepare-boot >/dev/null 2>&1 || true
fi

booted_ref=$(booted_image_reference) || {
    echo "rowyn-bootc-guard: could not determine the booted Rowyn image reference" >&2
    exit 1
}
# Already on the target channel's tag: a plain upgrade pulls its newest
# digest. Anything else Rowyn-shaped — the pre-move kyth-os namespace,
# the other channel's tag, a digest-pinned deployment — switches to the
# target ref explicitly.
if [[ "$booted_ref" == "$TARGET_REF" ]]; then
    timeout 3600 /usr/bin/bootc upgrade
else
    case "$booted_ref" in
        ghcr.io/kyth-os/rowyn:*|ghcr.io/kyth-os/rowyn@sha256:*|ghcr.io/mrtrickdotorg/rowyn:*|ghcr.io/mrtrickdotorg/rowyn@sha256:*)
            timeout 3600 /usr/bin/bootc switch "$TARGET_REF"
            ;;
        *)
            echo "rowyn-bootc-guard: unsupported booted Rowyn image reference" >&2
            exit 1
            ;;
    esac
fi
status=$?
if (( status != 0 )); then
    exit "$status"
fi

record_channel() {
    # Persist the selection where the watcher and rowyn-safe-upgrade
    # read it: the /etc policy wins over the shipped default, so record
    # there — seeding from the shipped default when no /etc file exists
    # so the recorded file stays complete. Best-effort: the switch has
    # already staged, so a failed record is reported, not fatal.
    local channel=$1
    local etc_file=${ROWYN_POLICY_ETC:-/etc/rowyn/update-policy.conf}
    local default_file=${ROWYN_POLICY_DEFAULT:-/usr/share/rowyn/update-policy.conf}
    if [[ ! -e "$etc_file" ]]; then
        mkdir -p "$(dirname "$etc_file")" || return 1
        if [[ -e "$default_file" ]]; then
            cp "$default_file" "$etc_file" || return 1
        else
            printf 'Channel=%s\n' "$channel" >"$etc_file" || return 1
            return 0
        fi
    fi
    if grep -q '^Channel=' "$etc_file"; then
        sed -i "s/^Channel=.*/Channel=$channel/" "$etc_file"
    else
        printf 'Channel=%s\n' "$channel" >>"$etc_file"
    fi
}

channel_name=stable
if [[ "$TARGET_REF" == "$TESTING_REF" ]]; then
    channel_name=testing
fi
if ! record_channel "$channel_name"; then
    echo "rowyn-bootc-guard: warning: could not record Channel=$channel_name in the /etc update policy; automatic staging may keep following the previous channel" >&2
fi

if [[ -x /usr/libexec/rowyn-finalize-staged ]]; then
    exec /usr/libexec/rowyn-finalize-staged
fi
exec /usr/bin/ostree admin finalize-staged
