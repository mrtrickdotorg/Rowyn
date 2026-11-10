#!/usr/bin/env bash
# rowyn-safe-upgrade — stage a Rowyn system update for the Hub.
#
# The Rowyn-side counterpart of the upstream safe-upgrade helper, reduced
# to the contract the Hub actually exercises, plus Rowyn's own update
# policy:
#
#   * the update policy (channel and downgrade permission) is the same
#     update-policy.conf the watcher reads, parsed with the same strict,
#     fail-closed rules; the channel (stable or testing) selects which
#     promoted tag of the canonical image this machine follows, and a
#     staged image older than the booted one is refused unless
#     AllowDowngrade=yes
#   * a refused downgrade is quarantined through rowyn-boot-health, so it
#     can never be treated as a healthy boot even if it still boots
#
#   * automatic staging (rowyn-update-watcher) sets ROWYN_UPDATE_AUTO=1:
#     then, and only then, the stage is deferred (exit 0, a "Deferred: ..."
#     line, nothing staged) while on battery power, on a metered
#     connection, or while another upgrade holds the lock. The watcher
#     exports ROWYN_ALLOW_BATTERY=1 / ROWYN_ALLOW_METERED=1 from the
#     policy file to waive a gate. A manual run (Hub) is never gated.
#   * invoked with no arguments, as root, via `sudo -A` from the Hub's
#     bootc_upgrade command (any argument is a usage error, exit 64)
#   * progress streams on stdout as marker lines
#         ROWYN_STAGE_PROGRESS pct=N phase=P detail=…
#     which the Hub's stage parser consumes verbatim — the ROWYN_ prefix
#     is part of that parser contract and is deliberately kept
#   * download occupies pct 1–85, deploy 86–99; 100 is never emitted, so
#     a killed or failed stage can never display as complete
#   * exit 0 on success (update staged, or already up to date) with the
#     result detail as the final stdout text; failures print to stderr
#     and exit non-zero with nothing recorded as staged
#   * the whole stage holds the bootc flock (/run/rowyn-bootc.lock),
#     shared with rowyn-bootc-guard, so two writers can never interleave
#   * SIGTERM (the Hub's cancel) terminates the bootc process group,
#     waits a 10s grace, then kills it — a terminated upgrade never
#     reports staged state
set -uo pipefail

readonly LOCK_FILE=/run/rowyn-bootc.lock
readonly IMAGE_NAME=ghcr.io/mrtrickdotorg/rowyn
# The ref this run stages: the canonical image at the policy channel's
# tag. Set definitively in main() once the policy is parsed.
update_ref=$IMAGE_NAME:stable
readonly BOOTC_TIMEOUT_SECS=3600
readonly TERM_GRACE_SECS=10
readonly SYSROOT_FREE_MIN_BYTES=$((2 * 1024 * 1024 * 1024))
readonly BOOT_HEALTH=${ROWYN_BOOT_HEALTH:-/usr/libexec/rowyn-boot-health}

# Update policy, read with the watcher's rules; the /etc file wins.
POLICY_ETC=${ROWYN_POLICY_ETC:-/etc/rowyn/update-policy.conf}
POLICY_DEFAULT=${ROWYN_POLICY_DEFAULT:-/usr/share/rowyn/update-policy.conf}
policy_channel=stable
policy_allow_downgrade=no
policy_error=

# Sysfs root and NetworkManager lookups are overridable for tests only.
POWER_SUPPLY_DIR=${ROWYN_POWER_SUPPLY_DIR:-/sys/class/power_supply}

workdir=
child=
cancelled=0

# Progress state (monotonic: the bar never moves backwards).
last_pct=0
last_phase=
last_detail=
blobs_needed=0
blobs_done=0
deploy_steps=0

cleanup() {
    if [[ -n "$workdir" ]]; then
        rm -rf "$workdir"
    fi
}
trap cleanup EXIT
trap 'cancelled=1' TERM INT HUP

emit_result() { printf 'ROWYN_UPDATE_RESULT outcome=%s reason=%s\n' "$1" "$2"; }

fail() {
    echo "$1" >&2
    case "$1" in
        *"runtime auth"*|*"runtime directory"*|*"auth file"*|*"Function not implemented"*) emit_result failed runtime_auth_io ;;
        *) emit_result failed upgrade_failed ;;
    esac
    exit 1
}

emit_marker() {
    local pct=$1 phase=$2 detail=$3
    if (( pct < last_pct )); then
        return 0
    fi
    if (( pct == last_pct )) && [[ "$phase" == "$last_phase" && "$detail" == "$last_detail" ]]; then
        return 0
    fi
    last_pct=$pct
    last_phase=$phase
    last_detail=$detail
    printf 'ROWYN_STAGE_PROGRESS pct=%s phase=%s detail=%s\n' "$pct" "$phase" "$detail"
}

check_free() {
    # Upstream parity: an unmeasurable path is not a failure; a measured
    # shortfall is.
    local path=$1 min_bytes=$2 avail
    avail=$(df --output=avail -B1 "$path" 2>/dev/null | tail -n 1 | tr -d '[:space:]')
    if [[ ! "$avail" =~ ^[0-9]+$ ]]; then
        return 0
    fi
    if (( avail < min_bytes )); then
        echo "Not enough free disk space on $path: $((avail / 1048576)) MiB free, need $((min_bytes / 1048576)) MiB" >&2
        return 1
    fi
    return 0
}

# True (0) when the machine is running on battery. A desktop (no battery
# device), an unreadable sysfs, or anything ambiguous is NOT on battery.
on_battery() {
    local dir=$POWER_SUPPLY_DIR d type online status
    local have_battery=0 have_mains=0 mains_online=0 discharging=0
    [[ -d "$dir" ]] || return 1
    for d in "$dir"/*; do
        [[ -r "$d/type" ]] || continue
        type=$(tr -d '[:space:]' <"$d/type" 2>/dev/null) || continue
        case "$type" in
            Battery)
                have_battery=1
                status=$(tr -d '[:space:]' <"$d/status" 2>/dev/null || true)
                [[ "$status" == Discharging ]] && discharging=1
                ;;
            Mains|USB*)
                online=$(tr -d '[:space:]' <"$d/online" 2>/dev/null || true)
                if [[ "$online" == 1 ]]; then
                    have_mains=1; mains_online=1
                elif [[ "$online" == 0 ]]; then
                    have_mains=1
                fi
                ;;
        esac
    done
    (( have_battery )) || return 1
    (( mains_online )) && return 1
    if (( have_mains )); then
        return 0
    fi
    # Battery but no readable adapter device: trust the battery's status.
    (( discharging ))
}

# True (0) when NetworkManager reports a metered connection (value 1 = yes,
# 3 = guess-yes). Missing nmcli/busctl or any error means "not metered".
on_metered_connection() {
    local out line
    if command -v nmcli >/dev/null 2>&1; then
        out=$(timeout 10 nmcli -g GENERAL.METERED dev show 2>/dev/null) || return 1
        while IFS= read -r line; do
            line=${line,,}
            case "$line" in
                yes|"yes (guessed)"|guess-yes|1|3) return 0 ;;
            esac
        done <<<"$out"
        return 1
    fi
    if command -v busctl >/dev/null 2>&1; then
        out=$(timeout 10 busctl get-property org.freedesktop.NetworkManager \
            /org/freedesktop/NetworkManager org.freedesktop.NetworkManager Metered 2>/dev/null) || return 1
        [[ "$out" == "u 1" || "$out" == "u 3" ]] && return 0
    fi
    return 1
}

# Same strict parser as rowyn-update-watcher: line-based key=value, never
# sourced or eval'd, and an unknown key, a bad value or a malformed line is
# a hard error. safe-upgrade reads it for the policy a manual Hub run would
# otherwise skip (the downgrade guard); AutoStage and the battery/metered
# gates stay the watcher's job.
parse_policy() {
    local file=$POLICY_DEFAULT line n=0 key val
    local re='^[[:space:]]*([A-Za-z]+)[[:space:]]*=[[:space:]]*([A-Za-z]+)[[:space:]]*$'
    [[ -e "$POLICY_ETC" ]] && file=$POLICY_ETC
    [[ -e "$file" ]] || return 0
    if [[ ! -r "$file" ]]; then
        policy_error="unreadable: $file"
        return 1
    fi
    while IFS= read -r line || [[ -n "$line" ]]; do
        n=$((n + 1))
        line=${line%$'\r'}
        if [[ "$line" =~ ^[[:space:]]*(#.*)?$ ]]; then
            continue
        fi
        if [[ ! "$line" =~ $re ]]; then
            policy_error="$file:$n: malformed line"
            return 1
        fi
        key=${BASH_REMATCH[1]}
        val=${BASH_REMATCH[2]}
        case "$key" in
            AutoStage|AllowOnBattery|AllowMetered|AllowDowngrade)
                case "$val" in
                    yes|no) ;;
                    *)
                        policy_error="$file:$n: bad value for $key (expected yes or no)"
                        return 1
                        ;;
                esac
                ;;
            Channel)
                case "$val" in
                    stable|testing) ;;
                    *)
                        policy_error="$file:$n: unsupported channel $val"
                        return 1
                        ;;
                esac
                ;;
            *)
                policy_error="$file:$n: unknown key $key"
                return 1
                ;;
        esac
        case "$key" in
            AllowDowngrade) policy_allow_downgrade=$val ;;
            Channel) policy_channel=$val ;;
        esac
    done <"$file"
    return 0
}

auto_gates() {
    if [[ "${ROWYN_ALLOW_BATTERY:-0}" != 1 ]] && on_battery; then
        echo "Deferred: running on battery power; will retry later."
        emit_result deferred battery
        exit 0
    fi
    if [[ "${ROWYN_ALLOW_METERED:-0}" != 1 ]] && on_metered_connection; then
        echo "Deferred: metered connection; will retry later."
        emit_result deferred metered
        exit 0
    fi
}

json_number() {
    # First "key":<digits> in a bootc progress JSON line. The closing
    # quote in the pattern keeps "bytes" from matching "bytesCached".
    local line=$1 key=$2
    local re="\"$key\":([0-9]+)"
    if [[ $line =~ $re ]]; then
        printf '%s' "${BASH_REMATCH[1]}"
    fi
}

handle_progress_json() {
    local line=$1
    if [[ "$line" == *'"task":"pulling"'* ]]; then
        if [[ "$line" == *'"type":"ProgressBytes"'* ]]; then
            local bytes total steps steps_total pct layer detail
            bytes=$(json_number "$line" bytes)
            total=$(json_number "$line" bytesTotal)
            steps=$(json_number "$line" steps)
            steps_total=$(json_number "$line" stepsTotal)
            if [[ -n "$total" && "$total" != 0 ]]; then
                pct=$((85 * ${bytes:-0} / total))
            elif [[ -n "$steps_total" && "$steps_total" != 0 ]]; then
                pct=$((85 * ${steps:-0} / steps_total))
            else
                return 0
            fi
            if (( pct < 1 )); then pct=1; fi
            if (( pct > 85 )); then pct=85; fi
            layer=$((${steps:-0} + 1))
            if (( layer > ${steps_total:-1} )); then layer=${steps_total:-1}; fi
            if [[ -n "$total" && "$total" != 0 ]]; then
                detail="Downloading layer $layer of ${steps_total:-1} · $(( ${bytes:-0} / 1048576 )) MiB of $(( total / 1048576 )) MiB"
            elif [[ -n "$steps_total" ]]; then
                detail="Downloading layer $layer of $steps_total"
            else
                detail="Downloading the update"
            fi
            emit_marker "$pct" download "$detail"
        elif [[ "$line" == *'"type":"ProgressSteps"'* ]]; then
            # A retried pull announces itself here; keep the bar where it
            # was and surface bootc's own wording.
            local re='"description":"([^"]*)"'
            if [[ "$line" =~ $re ]]; then
                local shown=$last_pct
                if (( shown < 1 )); then shown=1; fi
                emit_marker "$shown" download "${BASH_REMATCH[1]}"
            fi
        fi
    elif [[ "$line" == *'"task":"importing"'* && "$line" == *'"type":"ProgressSteps"'* ]]; then
        local steps steps_total
        steps=$(json_number "$line" steps)
        steps_total=$(json_number "$line" stepsTotal)
        if [[ -n "$steps" && -n "$steps_total" ]] && (( steps >= steps_total )); then
            emit_marker 88 install "Image downloaded, staging it"
        else
            emit_marker 86 install "Importing the image"
        fi
    fi
}

handle_bootc_text() {
    # Fallback progress from bootc's stderr text (also its only source
    # when an older bootc rejects --progress-fd): layer totals, per-blob
    # completions, and deploy-phase keywords.
    local line=${1,,}
    if [[ "$line" == *"layers needed:"* ]]; then
        local rest=${line#*layers needed:}
        rest=${rest#"${rest%%[![:space:]]*}"}
        local num=${rest%%[!0-9]*}
        if [[ "$num" =~ ^[0-9]+$ ]] && (( num > 0 )); then
            blobs_needed=$num
        fi
        return 0
    fi
    if [[ "$line" == "copying blob "* || "$line" == "copying config "* ]]; then
        if [[ "$line" == *" done"* || "$line" == *"already exists"* || "$line" == *"skipped"* ]]; then
            blobs_done=$((blobs_done + 1))
            if (( blobs_needed > 0 )); then
                local pct=$((85 * blobs_done / blobs_needed))
                if (( pct < 1 )); then pct=1; fi
                if (( pct > 85 )); then pct=85; fi
                emit_marker "$pct" download "Downloading layer $((blobs_done + 1)) of $blobs_needed"
            fi
        fi
        return 0
    fi
    case "$line" in
        *"writing manifest"*|*"storing signatures"*|*"already have manifest"*)
            emit_marker 85 download "Downloading the update"
            ;;
        *"merging layer"*|*"writing commit"*|*deploying*|*dracut*|*bootloader*|*"staged deployment"*|*"staged update"*)
            deploy_steps=$((deploy_steps + 1))
            local steps=$deploy_steps
            if (( steps > 7 )); then steps=7; fi
            emit_marker $((85 + steps * 2)) install "Installing the staged image"
            ;;
    esac
}

# Stream pump: fold newly appended bytes of $1 into the offset/buffer
# variables named by $2/$3 and dispatch each completed line to the
# handler named $4. Command substitution strips trailing newlines, so a
# sentinel keeps line boundaries intact across chunk boundaries; the
# unterminated remainder stays buffered for the next chunk (or the final
# flush in drain_all).
pump() {
    local file=$1
    local -n _offset=$2 _buffer=$3
    local handler=$4
    local size chunk line
    size=$(stat -c %s -- "$file" 2>/dev/null || printf '0')
    if (( size > _offset )); then
        chunk=$(tail -c "+$((_offset + 1))" -- "$file" | tr '\r' '\n'; printf '.')
        chunk=${chunk%.}
        _offset=$size
        _buffer+=$chunk
        while [[ "$_buffer" == *$'\n'* ]]; do
            line=${_buffer%%$'\n'*}
            _buffer=${_buffer#*$'\n'}
            "$handler" "$line"
        done
    fi
}

prog_offset=0
prog_buffer=
err_offset=0
err_buffer=

drain_progress() {
    pump "$workdir/bootc.progress" prog_offset prog_buffer handle_progress_json
    pump "$workdir/bootc.err" err_offset err_buffer handle_bootc_text
}

drain_all() {
    drain_progress
    if [[ -n "$prog_buffer" ]]; then
        handle_progress_json "$prog_buffer"
        prog_buffer=
    fi
    if [[ -n "$err_buffer" ]]; then
        handle_bootc_text "$err_buffer"
        err_buffer=
    fi
}

terminate_child() {
    # The child is a session leader (setsid): signal its whole group so
    # no forked bootc helper outlives the cancel.
    kill -TERM -- "-$child" 2>/dev/null || true
    local waited=0
    while kill -0 "$child" 2>/dev/null && (( waited < TERM_GRACE_SECS * 4 )); do
        sleep 0.25
        waited=$((waited + 1))
    done
    if kill -0 "$child" 2>/dev/null; then
        kill -KILL -- "-$child" 2>/dev/null || true
    fi
    wait "$child" 2>/dev/null || true
}

# Outcome of the last supervised run (globals, so a genuine bootc exit
# code can never collide with the cancellation/timeout sentinels).
child_status=0
child_timed_out=0

# Run `bootc switch` supervised: streams progress, enforces the timeout,
# honours cancellation; records the outcome in child_status /
# child_timed_out / cancelled.
run_bootc_update() {
    local operation=$1 with_progress_fd=$2
    local -a args
    case "$operation" in
        switch) args=(switch "$update_ref") ;;
        upgrade) args=(upgrade) ;;
        *) fail "Unsupported bootc update operation" ;;
    esac
    : >"$workdir/bootc.out"
    : >"$workdir/bootc.err"
    : >"$workdir/bootc.progress"
    # shellcheck disable=SC2034  # read and written by name in pump()
    prog_offset=0
    prog_buffer=
    # shellcheck disable=SC2034
    err_offset=0
    err_buffer=
    if (( with_progress_fd )); then
        # bootc wraps --progress-fd in tokio's unix::pipe::Sender, which
        # rejects anything that is not a real pipe ("not a pipe") — a plain
        # file redirect does not work. Hand it the write end of a pipe whose
        # reader appends to the progress file that drain_progress tails.
        if [[ "$operation" == switch ]]; then
            args=(switch --progress-fd 3 "$update_ref")
        else
            args=(upgrade --progress-fd 3)
        fi
        setsid /usr/bin/bootc "${args[@]}" \
            >"$workdir/bootc.out" 2>"$workdir/bootc.err" \
            3> >(cat >>"$workdir/bootc.progress") &
    else
        setsid /usr/bin/bootc "${args[@]}" \
            >"$workdir/bootc.out" 2>"$workdir/bootc.err" &
    fi
    child=$!
    child_status=0
    child_timed_out=0
    local started=$SECONDS
    while kill -0 "$child" 2>/dev/null; do
        drain_progress
        if (( cancelled )); then
            terminate_child
            child_status=1
            return 0
        fi
        if (( SECONDS - started >= BOOTC_TIMEOUT_SECS )); then
            terminate_child
            child_timed_out=1
            child_status=1
            return 0
        fi
        sleep 0.25
    done
    wait "$child"
    child_status=$?
    # The pipe reader appends asynchronously; let it flush the last lines.
    sleep 0.3
    drain_all
    return 0
}

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

result_detail() {
    # bootc's own words are the job detail (the upstream helper relays the
    # same text,
    # redacted and capped; the cap is kept, the redaction patterns are
    # upstream-service-specific and not ported).
    local detail
    detail=$(cat "$workdir/bootc.out" "$workdir/bootc.err" 2>/dev/null \
        | tr -d '\r' | awk 'NF' | tail -n 5 | head -c 2000)
    printf '%s' "$detail"
}

main() {
    if (( $# > 0 )); then
        echo "rowyn-safe-upgrade accepts no arguments" >&2
        exit 64
    fi
    if (( EUID != 0 )); then
        fail "rowyn-safe-upgrade must run as root"
    fi
    if [[ ! -x /usr/bin/bootc ]]; then
        fail "bootc is not installed on this system"
    fi
    if ! parse_policy; then
        fail "refusing to stage: invalid update policy (${policy_error:-unreadable})"
    fi
    update_ref="$IMAGE_NAME:$policy_channel"

    if [[ "${ROWYN_UPDATE_AUTO:-0}" == 1 ]]; then
        auto_gates
    fi

    workdir=$(mktemp -d)

    # Only the physical root needs headroom here. Rowyn boots composefs with
    # systemd-boot: /boot is an empty directory on the read-only root (df
    # reports 0 free), and bootc mounts the ESP itself and reports a short ESP.
    check_free /sysroot "$SYSROOT_FREE_MIN_BYTES" || exit 1

    exec 9>"$LOCK_FILE"
    if ! flock -n 9; then
        if [[ "${ROWYN_UPDATE_AUTO:-0}" == 1 ]]; then
            echo "Deferred: another bootc upgrade is in progress; will retry later."
            emit_result deferred upgrade_locked
            exit 0
        fi
        fail "Another bootc upgrade is in progress; retry later"
    fi

    local booted_ref operation
    booted_ref=$(booted_image_reference) || fail "Could not determine the booted Rowyn image reference"
    # Already on the followed channel's tag: upgrade pulls its newest
    # digest. Anything else Rowyn-shaped — the pre-move kyth-os
    # namespace, the other channel's tag, a digest-pinned deployment —
    # switches to the followed channel's ref explicitly.
    if [[ "$booted_ref" == "$update_ref" ]]; then
        operation=upgrade
    else
        case "$booted_ref" in
            ghcr.io/kyth-os/rowyn:*|ghcr.io/kyth-os/rowyn@sha256:*|ghcr.io/mrtrickdotorg/rowyn:*|ghcr.io/mrtrickdotorg/rowyn@sha256:*) operation=switch ;;
            *) fail "Unsupported booted Rowyn image reference" ;;
        esac
    fi

    emit_marker 1 download "Starting image download"

    run_bootc_update "$operation" 1
    if (( ! cancelled && ! child_timed_out && child_status != 0 )); then
        # A bootc that predates --progress-fd rejects it before doing any
        # work. Staging without live byte progress beats not staging.
        if grep -qE "unexpected argument '--progress-fd'|not a pipe" "$workdir/bootc.err"; then
            blobs_needed=0
            blobs_done=0
            deploy_steps=0
            run_bootc_update "$operation" 0
        fi
    fi
    if (( cancelled )); then
        fail "bootc update was terminated and did not complete; nothing was staged"
    fi
    if (( child_timed_out )); then
        fail "bootc update timed out; retry later"
    fi
    if (( child_status != 0 )); then
        local detail
        detail=$(result_detail)
        if [[ -n "$detail" ]]; then
            fail "$detail"
        fi
        fail "bootc update failed (exit code $child_status)"
    fi

    # Did anything actually stage? bootc exits 0 for "no changes" too,
    # and finalizing an empty stage would be an error of its own.
    local status_json compact
    if ! status_json=$(timeout 30 /usr/bin/bootc status --json 2>/dev/null); then
        fail "Could not determine the staged update state after bootc upgrade"
    fi
    compact=${status_json//[[:space:]]/}
    if [[ "$compact" != *'"staged":{'* ]]; then
        local detail
        detail=$(result_detail)
        if [[ -n "$detail" ]]; then
            printf '%s\n' "$detail"
        else
            echo "Rowyn is already running the latest image."
        fi
        emit_result current no_update
        exit 0
    fi

    emit_marker 99 verify "Verifying the staged image and checking its safety policy"
    # A staged image older than the booted one is a policy failure, not a
    # health one. Refuse it before the deployment is finalized for the next
    # boot, and quarantine the digest so a boot into it is rolled back.
    if [[ -x "$BOOT_HEALTH" ]]; then
        local -a downgrade_args=()
        if [[ "$policy_allow_downgrade" == "yes" || "${ROWYN_ALLOW_DOWNGRADE:-0}" == "1" ]]; then
            downgrade_args=(--allow)
        fi
        if ! "$BOOT_HEALTH" refuse-downgrade "${downgrade_args[@]}"; then
            fail "The available image is older than the booted one and AllowDowngrade is not enabled; nothing was finalized"
        fi
    fi
    emit_marker 99 finalize "Preparing the staged deployment for the next boot"
    if [[ -x /usr/libexec/rowyn-finalize-staged ]]; then
        if ! /usr/libexec/rowyn-finalize-staged; then
            fail "The staged update could not be finalized for the next boot"
        fi
    elif [[ -x /usr/bin/ostree ]]; then
        if ! /usr/bin/ostree admin finalize-staged; then
            fail "The staged update could not be finalized for the next boot"
        fi
    else
        fail "No staged-update finalizer is installed on this system"
    fi

    local detail
    detail=$(result_detail)
    if [[ -n "$detail" ]]; then
        printf '%s\n' "$detail"
    else
        echo "Update staged and ready to apply on the next boot."
    fi
    emit_result staged update_staged
    exit 0
}

main "$@"
