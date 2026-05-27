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
# Docker access: honours $DOCKER_HOST. In the stack the api points it at the
# scoped docker-socket-proxy (DOCKER_HOST=tcp://docker-proxy:2375), so the api
# never holds the raw socket. The flow uses ONLY create / start / wait / logs /
# rm + image-inspect — the proxy's allowlist — and runs the container DETACHED,
# passing the snippet via an env var so it never needs the attach/exec endpoints.
#
# Contract (see ops/sandbox/README.md):
#   stdin  : UTF-8 Python source (the snippet to run)
#   stdout : one line of JSON —
#            {ok, timed_out, exit_code, stdout, stderr, truncated, error, duration_ms}
#   exit   : 0 when a sandbox verdict was produced (success, user error, OR
#            timeout/kill — all are valid verdicts). Non-zero (3) only on
#            launcher INFRA failure (docker/image unavailable), still with JSON.
#
# Tunables (env): SANDBOX_IMAGE, SANDBOX_TIMEOUT (also $1), SANDBOX_GRACE,
#   SANDBOX_MEMORY, SANDBOX_CPUS, SANDBOX_PIDS, SANDBOX_OUTPUT_MAX.

set -uo pipefail

IMAGE="${SANDBOX_IMAGE:-johnny5-sandbox:latest}"
TIMEOUT="${1:-${SANDBOX_TIMEOUT:-10}}"     # in-container graceful timeout (seconds)
GRACE="${SANDBOX_GRACE:-5}"                # extra seconds before the launcher's hard backstop
HARD=$((TIMEOUT + GRACE))                  # backstop: fires only if the in-container guard is wedged
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
# Image inspect (GET /images/.../json) — also confirms the image is present so a
# detached run never silently tries to pull (pull is off the proxy allowlist).
docker image inspect "$IMAGE" >/dev/null 2>&1 || {
    emit_infra_error "SandboxUnavailable" "sandbox image ${IMAGE} missing — run ./ctl.sh sandbox-build" 125
    exit 3
}

# Read the snippet and hand it to the container via env (base64) — keeps the run
# detached (no attach endpoint) and immune to shell/CLI interpretation.
src="$(cat)"
code_b64="$(printf '%s' "$src" | base64 -w0)"

name="johnny5-sandbox-$$-${RANDOM}-$(date +%s%N)"
out="$(mktemp)"
err="$(mktemp)"
cleanup() {
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
# (no bind mounts: the container can't see the repo, .env, or data/)
# All of the above are create-time HostConfig options, forwarded verbatim through
# the proxy's /containers/create — the proxy never strips them.
if ! docker run -d --name "$name" \
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
        -e SANDBOX_CODE_B64="$code_b64" \
        -e SANDBOX_RUN_TIMEOUT="$TIMEOUT" \
        -e SANDBOX_OUTPUT_MAX="$OUTPUT_MAX" \
        "$IMAGE" >/dev/null 2>"$err"; then
    errtext="$(head -c 1500 "$err" 2>/dev/null | tr '\n\r\t' '   ' | sed 's/\\/\\\\/g; s/"/\\"/g')"
    emit_infra_error "SandboxUnavailable" "failed to start sandbox: ${errtext}" 125
    exit 3
fi

# Block until exit, with a hard backstop. `docker wait` prints the container's
# exit code and returns it. If the backstop fires it kills the WAIT (not the
# container) → $ec is empty → we force-remove the wedged container. The group's
# 2>/dev/null swallows the shell's "Killed" job notice if the wait is SIGKILLed.
t0="$(date +%s%3N)"
ec="$( { timeout --signal=KILL "${HARD}s" docker wait "$name"; } 2>/dev/null )"
elapsed="$(( $(date +%s%3N) - t0 ))"

if [ -z "$ec" ]; then
    # Backstop fired: the in-container guard was wedged (e.g. a C-level loop).
    docker rm -f "$name" >/dev/null 2>&1 || true
    printf '{"ok":false,"timed_out":true,"exit_code":124,"stdout":"","stderr":"killed: wedged past the %ss limit (hard backstop)","error":{"type":"Timeout","message":"sandbox exceeded its %ss limit and was force-killed"},"duration_ms":%s}\n' \
        "$TIMEOUT" "$TIMEOUT" "$elapsed"
    exit 0
fi

# Container exited on its own. Pull its captured streams — container stdout (the
# runner's verdict) → $out, container stderr → $err — then remove it.
docker logs "$name" >"$out" 2>"$err"
docker rm -f "$name" >/dev/null 2>&1 || true

# The runner exits 0 whenever it ran to completion (success, user exception, or
# its OWN in-container timeout — all encoded in the verdict JSON). A non-zero
# container exit means the kernel killed it before it could emit: 137 = SIGKILL
# (OOM / pids / cpu cap), 139 = SIGSEGV.
if [ "$ec" = "0" ] && [ -s "$out" ]; then
    cat "$out"
    exit 0
fi
if [ "$ec" = "137" ] || [ "$ec" = "139" ]; then
    printf '{"ok":false,"timed_out":false,"exit_code":%s,"stdout":"","stderr":"killed by SIGKILL — memory (OOM) or resource cap","error":{"type":"Killed","message":"sandbox terminated by the kernel resource guard (memory/pids/cpu cap)"},"duration_ms":%s}\n' \
        "$ec" "$elapsed"
    exit 0
fi
# Non-zero exit but the runner still left a verdict — trust it.
if [ -s "$out" ]; then
    cat "$out"
    exit 0
fi
# No verdict at all (near-impossible). Surface stderr for diagnosis.
errtext="$(head -c 1500 "$err" 2>/dev/null | tr '\n\r\t' '   ' | sed 's/\\/\\\\/g; s/"/\\"/g')"
printf '{"ok":false,"timed_out":false,"exit_code":%s,"stdout":"","stderr":"%s","error":{"type":"SandboxError","message":"runner produced no verdict (exit=%s)"},"duration_ms":%s}\n' \
    "$ec" "$errtext" "$ec" "$elapsed"
exit 0
