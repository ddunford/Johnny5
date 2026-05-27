#!/usr/bin/env bash
#
# code_exec sandbox launcher — the SECURITY BOUNDARY (TASK-6b.6a).
#
# Reads Python source on STDIN, runs it once in a throw-away hardened
# `johnny5-sandbox` container, and prints exactly ONE JSON result envelope on
# STDOUT. Every host-protection flag lives HERE (owned by devops, audited by the
# security review) so the boundary is in one place — the calling tool never has
# to be trusted to pass the right flags.
#
# Contract (see ops/sandbox/README.md):
#   stdin  : UTF-8 Python source (the snippet to run)
#   stdout : one line of JSON —
#            {ok, timed_out, exit_code, stdout, stderr, truncated, error, duration_ms}
#   exit   : 0 when a sandbox verdict was produced (success, user error, OR
#            timeout/kill — all are valid verdicts). Non-zero (3) only on
#            launcher INFRA failure (docker/image unavailable), still with JSON.
#
# Tunables (env): SANDBOX_IMAGE, SANDBOX_TIMEOUT (also $1), SANDBOX_MEMORY,
#   SANDBOX_CPUS, SANDBOX_PIDS, SANDBOX_OUTPUT_MAX.

set -uo pipefail

IMAGE="${SANDBOX_IMAGE:-johnny5-sandbox:latest}"
TIMEOUT="${1:-${SANDBOX_TIMEOUT:-10}}"     # in-container graceful timeout (seconds)
GRACE="${SANDBOX_GRACE:-5}"                # extra seconds before the launcher's hard SIGKILL backstop
HARD=$((TIMEOUT + GRACE))                  # launcher backstop: fires only if the runner is wedged
MEMORY="${SANDBOX_MEMORY:-256m}"
CPUS="${SANDBOX_CPUS:-1.0}"
PIDS="${SANDBOX_PIDS:-128}"
OUTPUT_MAX="${SANDBOX_OUTPUT_MAX:-65536}"

emit_infra_error() {  # type message exit_code
    printf '{"ok":false,"timed_out":false,"exit_code":%s,"stdout":"","stderr":"","error":{"type":"%s","message":"%s"},"duration_ms":0}\n' \
        "$3" "$1" "$2"
}

command -v docker >/dev/null 2>&1 || {
    emit_infra_error "SandboxUnavailable" "docker CLI not available to the launcher" 127
    exit 3
}
docker image inspect "$IMAGE" >/dev/null 2>&1 || {
    emit_infra_error "SandboxUnavailable" "sandbox image ${IMAGE} missing — run ./ctl.sh sandbox-build" 125
    exit 3
}

name="johnny5-sandbox-$$-${RANDOM}-$(date +%s%N)"
out="$(mktemp)"
err="$(mktemp)"
cleanup() {
    # `--rm` covers the clean path; this also reaps a container left behind if the
    # launcher (or its `timeout`) is killed before docker could remove it.
    docker rm -f "$name" >/dev/null 2>&1 || true
    rm -f "$out" "$err"
}
trap cleanup EXIT INT TERM

# --- the boundary -----------------------------------------------------------
# --network none      : no egress at all (kills SSRF/exfil/pip-install)
# --cap-drop ALL      : no Linux capabilities
# --security-opt no-new-privileges : setuid binaries can't escalate
# --read-only + tmpfs : immutable rootfs; only a small noexec/nosuid /tmp is writable
# --user 65534:65534  : nobody:nogroup — never root, even if the image USER changed
# --memory/--cpus/--pids-limit : resource caps (OOM-kill, throttle, fork-bomb cap)
# --ipc none / --cgroupns private : namespace isolation
# timeout --signal=KILL "${HARD}s" : backstop SIGKILL if the in-container guard
#   is wedged (e.g. a C-level loop ignoring signals). The runner's own SANDBOX_RUN
#   _TIMEOUT (= $TIMEOUT) normally fires first and yields a graceful verdict; this
#   only bites GRACE seconds later.
# The group's `2>/dev/null` swallows the shell's own job-termination notice
# ("…: Killed") when the container is SIGKILLed; docker's real stdout/stderr are
# captured by the inner redirections to $out/$err and are unaffected.
t0="$(date +%s%3N)"
{ timeout --signal=KILL "${HARD}s" \
    docker run --rm --name "$name" \
        --network none \
        --memory "$MEMORY" --memory-swap "$MEMORY" --memory-swappiness 0 \
        --cpus "$CPUS" \
        --pids-limit "$PIDS" \
        --user 65534:65534 \
        --read-only \
        --tmpfs /tmp:rw,noexec,nosuid,nodev,size=16m \
        --cap-drop ALL \
        --security-opt no-new-privileges \
        --ipc none \
        --cgroupns private \
        -i \
        -e SANDBOX_RUN_TIMEOUT="$TIMEOUT" \
        -e SANDBOX_OUTPUT_MAX="$OUTPUT_MAX" \
        "$IMAGE" >"$out" 2>"$err"; rc=$?; } 2>/dev/null
elapsed="$(( $(date +%s%3N) - t0 ))"

# Happy / user-error / graceful-timeout path: the runner ran to completion and
# emitted its JSON verdict (it exits 0 even for user exceptions and its OWN
# in-container timeout). Pass that authoritative verdict straight through.
if [ "$rc" -eq 0 ] && [ -s "$out" ]; then
    cat "$out"
    exit 0
fi

# rc != 0 ⇒ the container was killed externally (our backstop) or died on its own
# (kernel OOM-killer for the memory cap, pids cap, or a crash). The runner never
# got to emit. Disambiguate by how long it ran: a kill at/near the hard backstop
# is a (wedged) timeout; a kill well before it is a resource/OOM kill.
docker rm -f "$name" >/dev/null 2>&1 || true
if [ "$rc" -eq 124 ] || { { [ "$rc" -eq 137 ] || [ "$rc" -eq 139 ]; } && [ "$elapsed" -ge "$(( HARD * 1000 - 750 ))" ]; }; then
    printf '{"ok":false,"timed_out":true,"exit_code":124,"stdout":"","stderr":"killed: wedged past the %ss limit (hard backstop)","error":{"type":"Timeout","message":"sandbox exceeded its %ss limit and was force-killed"},"duration_ms":%s}\n' \
        "$TIMEOUT" "$TIMEOUT" "$elapsed"
    exit 0
fi
if [ "$rc" -eq 137 ] || [ "$rc" -eq 139 ]; then
    printf '{"ok":false,"timed_out":false,"exit_code":%s,"stdout":"","stderr":"killed by SIGKILL — memory (OOM) or resource cap","error":{"type":"Killed","message":"sandbox terminated by the kernel resource guard (memory/pids/cpu cap)"},"duration_ms":%s}\n' \
        "$rc" "$elapsed"
    exit 0
fi

# Some other non-zero rc but the runner still left a verdict — trust it.
if [ -s "$out" ]; then
    cat "$out"
    exit 0
fi

# Runner crashed before emitting (should be near-impossible). Surface stderr.
errtext="$(head -c 1500 "$err" 2>/dev/null | tr '\n\r\t' '   ' | sed 's/\\/\\\\/g; s/"/\\"/g')"
printf '{"ok":false,"timed_out":false,"exit_code":%s,"stdout":"","stderr":"%s","error":{"type":"SandboxError","message":"runner produced no verdict (rc=%s)"},"duration_ms":0}\n' \
    "$rc" "$errtext" "$rc"
exit 0
