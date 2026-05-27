#!/usr/bin/env bash
#
# Johnny 5 — control script. The single entry point for every dev/ops task.
#
#   ./ctl.sh help
#
# Rules (house convention):
#   - Never bypass with raw `docker compose`. If a command is missing, ADD it.
#     This script is the API of the project.
#   - Tests NEVER touch the dev database. `test` forces johnny5_test.
#   - Production mode is opt-in: a `.production` marker file or PRODUCTION_MODE=true.
#     In prod, dev-only commands (test, reset, rebuild) are blocked and `migrate`
#     runs non-interactively.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Load .env so ctl.sh's own helpers (test-DB URL, psql creds) match what
# docker compose interpolates. compose reads .env on its own too.
if [ -f .env ]; then
    set -a
    # shellcheck disable=SC1091
    . ./.env
    set +a
fi

# ---------------------------------------------------------------------------
# Style
# ---------------------------------------------------------------------------
if [ -t 1 ]; then
    RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
    BLUE='\033[0;34m'; DIM='\033[2m'; NC='\033[0m'
else
    RED=''; GREEN=''; YELLOW=''; BLUE=''; DIM=''; NC=''
fi
log_info()    { printf '%b[INFO]%b %s\n'  "$BLUE"   "$NC" "$*"; }
log_success() { printf '%b[OK]%b %s\n'    "$GREEN"  "$NC" "$*"; }
log_warn()    { printf '%b[WARN]%b %s\n'  "$YELLOW" "$NC" "$*"; }
log_error()   { printf '%b[ERROR]%b %s\n' "$RED"    "$NC" "$*" >&2; }

confirm() {
    local prompt="$1"
    printf '%b%s%b [y/N] ' "$YELLOW" "$prompt" "$NC"
    read -r reply
    case "$reply" in
        [yY]|[yY][eE][sS]) return 0 ;;
        *) log_warn "Aborted."; return 1 ;;
    esac
}

# ---------------------------------------------------------------------------
# Mode
# ---------------------------------------------------------------------------
PRODUCTION_MODE=false
if [ -f "$SCRIPT_DIR/.production" ] || [ "${PRODUCTION_MODE:-}" = "true" ]; then
    PRODUCTION_MODE=true
fi

dc() {
    if [ "$PRODUCTION_MODE" = true ]; then
        docker compose -f docker-compose.yml -f docker-compose.production.yml "$@"
    else
        docker compose "$@"
    fi
}

ensure_prod_off() {
    if [ "$PRODUCTION_MODE" = true ]; then
        log_error "'$1' is blocked in production mode."
        exit 1
    fi
}

# Wait until a compose service reports healthy (services with a healthcheck).
wait_for_healthy() {
    local svc="$1" max="${2:-90}" waited=0 state
    log_info "Waiting for '$svc' to be healthy (max ${max}s)..."
    while [ "$waited" -lt "$max" ]; do
        state="$(dc ps "$svc" --format '{{.Health}}' 2>/dev/null || true)"
        case "$state" in
            healthy) log_success "'$svc' is healthy."; return 0 ;;
            unhealthy) log_error "'$svc' is unhealthy."; dc logs --tail=40 "$svc"; return 1 ;;
        esac
        sleep 2; waited=$((waited + 2))
    done
    log_error "'$svc' did not become healthy within ${max}s."
    dc logs --tail=40 "$svc"
    return 1
}

# ---------------------------------------------------------------------------
# Test isolation — per-run datastore slots (the 6b concurrency guard).
#
# Two `./ctl.sh test` runs that share the single `johnny5_test` DB interleave
# their per-function TRUNCATEs and corrupt each other (Phase-6a incident: 17
# phantom failures from a collision). Fix: each run claims an exclusive *slot*
# (1..N) via `flock`, and the slot maps 1:1 to an isolated Postgres DB
# (`johnny5_test_be_<slot>`) + a distinct Redis logical db (index = slot) + a
# deterministically-named run container (`johnny5-test-run-<slot>`). Different
# slots can never touch the same DB/Redis db, so parallel runs are safe; the
# flock auto-releases when the launching shell dies, so a crashed run frees its
# slot. A leaked/SIGKILLed run's orphan container is reaped before slot reuse.
# ---------------------------------------------------------------------------
TEST_SLOT_COUNT=15                                      # Redis ships 16 dbs (0..15); 0 is dev, so 1..15.
TEST_SLOT_WAIT="${TEST_SLOT_WAIT:-1800}"                # seconds to queue if all slots are busy
TEST_DB_PREFIX="johnny5_test_be"                        # per-slot DB name prefix (contains "test" → passes the migrate guard)
TEST_LOCK_DIR="${TMPDIR:-/tmp}/johnny5-test-slots"
TEST_LOCK_FD=200                                        # fixed fd held for the run; closes (releases lock) on shell exit
TEST_SLOT=""                                            # set by acquire_test_slot
TEST_RUN_CONTAINER=""                                   # set by cmd_test once the slot is known
TEST_RUN_PID=""                                         # PID of the backgrounded `docker compose run`

# Claim an exclusive test slot. Scans 1..N non-blocking and takes the first
# free one; if every slot is busy it queues (blocking) on slot 1 up to
# TEST_SLOT_WAIT. Holds the lock on fd $TEST_LOCK_FD for the run's lifetime —
# when this shell exits (normally, on a trapped signal, or via OS cleanup after
# SIGKILL) the fd closes and the lock releases, so a dead run never wedges a slot.
acquire_test_slot() {
    mkdir -p "$TEST_LOCK_DIR"
    local n
    for n in $(seq 1 "$TEST_SLOT_COUNT"); do
        eval "exec ${TEST_LOCK_FD}>\"${TEST_LOCK_DIR}/slot-${n}.lock\""
        if flock -n "$TEST_LOCK_FD"; then
            TEST_SLOT="$n"
            return 0
        fi
    done
    log_warn "All ${TEST_SLOT_COUNT} test slots are busy — queuing for one (up to ${TEST_SLOT_WAIT}s)..."
    eval "exec ${TEST_LOCK_FD}>\"${TEST_LOCK_DIR}/slot-1.lock\""
    if flock -w "$TEST_SLOT_WAIT" "$TEST_LOCK_FD"; then
        TEST_SLOT="1"
        return 0
    fi
    return 1
}

# Create the per-slot test database on the running postgres if it is missing.
# Uses the dev superuser; the test DB lives on the same server but is a separate
# database, so dev data is never touched. Reused across sequential runs in the
# same slot (the per-test TRUNCATE + the session-scoped `alembic upgrade head`
# keep it clean and current).
ensure_test_db() {
    local dbname="$1" user="${POSTGRES_USER:-johnny5}"
    if dc exec -T postgres psql -U "$user" -tAc \
        "SELECT 1 FROM pg_database WHERE datname='${dbname}'" 2>/dev/null | grep -q 1; then
        return 0
    fi
    log_info "Creating test database '${dbname}'..."
    dc exec -T postgres createdb -U "$user" -O "$user" "${dbname}"
}

# Cleanup for a test run: force-remove the run container (so a trapped
# SIGTERM/SIGINT — e.g. TaskStop/Ctrl-C — can't leave a detached pytest hammering
# the DB) and flush this slot's Redis db. `--rm` already removes the container on
# a clean exit; this makes the interrupted path deterministic too. The slot lock
# releases when the shell exits and fd $TEST_LOCK_FD closes.
cleanup_test_run() {
    # Stop the compose CLI first so it can't (re)create the container in the
    # narrow window between launch and container creation, then force-remove the
    # container (kills pytest inside), then flush the slot's Redis db.
    [ -n "$TEST_RUN_PID" ] && kill "$TEST_RUN_PID" 2>/dev/null || true
    [ -n "$TEST_RUN_CONTAINER" ] && docker rm -f "$TEST_RUN_CONTAINER" >/dev/null 2>&1 || true
    [ -n "$TEST_SLOT" ] && dc exec -T redis redis-cli -n "$TEST_SLOT" flushdb >/dev/null 2>&1 || true
}

# Reap run containers left behind by a SIGKILLed launcher (no trap could fire).
# A container is an orphan only if its slot lock is FREE — a live run holds its
# lock, so `flock -n` fails and we leave it strictly alone. Safe to call before
# claiming our own slot. Requires $TEST_LOCK_DIR to exist.
reap_orphan_test_containers() {
    local n cname probe_fd=201
    for n in $(seq 1 "$TEST_SLOT_COUNT"); do
        cname="johnny5-test-run-${n}"
        docker ps -a --format '{{.Names}}' 2>/dev/null | grep -qx "$cname" || continue
        eval "exec ${probe_fd}>\"${TEST_LOCK_DIR}/slot-${n}.lock\""
        if flock -n "$probe_fd"; then
            log_warn "Reaping orphan test container '${cname}' (slot ${n} is free → its run is dead)."
            docker rm -f "$cname" >/dev/null 2>&1 || true
        fi
        eval "exec ${probe_fd}>&-"
    done
}

# ---------------------------------------------------------------------------
# code_exec sandbox — hardened image for running hostile Python (Phase 6b).
# Not a compose service: it's launched per-exec by ops/sandbox/run-sandbox.sh
# (the security boundary). ctl.sh just builds it and can self-test the boundary.
# ---------------------------------------------------------------------------
SANDBOX_IMAGE="johnny5-sandbox:latest"
SANDBOX_DIR="$SCRIPT_DIR/ops/sandbox"
SANDBOX_LAUNCHER="$SANDBOX_DIR/run-sandbox.sh"

# Build the sandbox image only if it's missing (used by `up` so a started stack
# can always run code_exec). `sandbox-build` / `rebuild` force a fresh build.
ensure_sandbox_image() {
    if ! docker image inspect "$SANDBOX_IMAGE" >/dev/null 2>&1; then
        log_info "Building code_exec sandbox image (${SANDBOX_IMAGE})..."
        docker build -t "$SANDBOX_IMAGE" "$SANDBOX_DIR" >/dev/null
        log_success "Sandbox image built."
    fi
}

# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

cmd_up() {            # Build if needed and start the stack
    log_info "Starting stack ($([ "$PRODUCTION_MODE" = true ] && echo production || echo development))..."
    # `dc up -d` builds any missing image — including the web SPA image
    # (multi-stage: vite build -> nginx) on first run.
    dc up -d "$@"
    wait_for_healthy postgres
    wait_for_healthy redis
    wait_for_healthy api || { log_error "API failed health check."; exit 1; }
    wait_for_healthy web || { log_error "Web (SPA) failed health check."; exit 1; }
    # The code_exec tool launches this image per-exec; ensure it exists.
    ensure_sandbox_image
    log_success "Stack up. UI: https://${PUBLIC_DOMAIN:-johnny.demosrv.uk}/  |  Health: ./ctl.sh health"
}

cmd_down() {          # Stop and remove containers (data volumes kept)
    log_info "Stopping stack..."
    dc down "$@"
    log_success "Stopped. Data in ./pgdata, ./redisdata kept (./ctl.sh reset to wipe)."
}

cmd_restart() {       # Restart one service or the whole stack
    dc restart "$@"
    log_success "Restarted ${*:-all services}."
}

cmd_build() {         # Build images
    dc build "$@"
    cmd_sandbox_build
    log_success "Build complete."
}

cmd_rebuild() {       # Rebuild images from scratch and recreate (dev only)
    ensure_prod_off rebuild
    dc build --no-cache "$@"
    cmd_sandbox_build --no-cache
    dc up -d --force-recreate "$@"
    log_success "Rebuilt and recreated ${*:-all services}."
}

cmd_logs() {          # Tail logs: ./ctl.sh logs [service]
    dc logs -f --tail=120 "$@"
}

cmd_status() {        # Show container + health status
    dc ps
}

cmd_shell() {         # Shell into a service (default: api)
    local svc="${1:-api}"; shift || true
    dc exec "$svc" bash 2>/dev/null || dc exec "$svc" sh
}

cmd_db() {            # Interactive psql into the dev database
    dc exec postgres psql -U "${POSTGRES_USER:-johnny5}" -d "${POSTGRES_DB:-johnny5}"
}

cmd_repl() {          # Open the cockpit: watch Johnny think, speak to him, pause/step
    log_info "Attaching cockpit (Ctrl-D to leave; Johnny keeps thinking)..."
    # Interactive (-it) so the consciousness feed streams and stdin reaches him.
    dc exec api uv run --frozen python -m repl
}

cmd_migrate() {       # Apply database migrations (alembic upgrade head)
    log_info "Bringing postgres up for migrations..."
    dc up -d postgres
    wait_for_healthy postgres
    log_info "Running: alembic upgrade head"
    dc run --rm --no-deps api uv run --frozen alembic upgrade head
    log_success "Migrations applied."
}

cmd_test() {          # Run the test suite against an isolated per-run slot (NEVER the dev DB)
    ensure_prod_off test
    log_info "Starting datastores for tests..."
    dc up -d postgres redis
    wait_for_healthy postgres
    wait_for_healthy redis

    # Sweep up any run container orphaned by a SIGKILLed launcher before we pick
    # a slot (only touches containers whose slot lock is free — never a live run).
    mkdir -p "$TEST_LOCK_DIR"
    reap_orphan_test_containers

    # Claim an exclusive slot so concurrent runs can't collide on the test DB.
    if ! acquire_test_slot; then
        log_error "Could not claim a free test slot within ${TEST_SLOT_WAIT}s — too many concurrent runs."
        log_error "Check for stray runners: docker ps | grep johnny5-test-run"
        exit 1
    fi

    local slot_db="${TEST_DB_PREFIX}_${TEST_SLOT}"
    TEST_RUN_CONTAINER="johnny5-test-run-${TEST_SLOT}"
    # Reap any orphan container left in this slot by a previously SIGKILLed run
    # (its launcher died, releasing the lock, but `--rm` couldn't fire) before we
    # reuse the slot's DB — closes the only remaining same-slot collision window.
    docker rm -f "$TEST_RUN_CONTAINER" >/dev/null 2>&1 || true
    # Tear down the container + flush the slot's Redis db on any exit (incl.
    # trapped SIGTERM/SIGINT). The slot lock frees when this shell exits.
    trap cleanup_test_run EXIT INT TERM

    ensure_test_db "$slot_db"
    local user="${POSTGRES_USER:-johnny5}" pass="${POSTGRES_PASSWORD:-}"
    local host="${POSTGRES_HOST:-postgres}" port="${POSTGRES_PORT:-5432}"
    local test_url="postgresql+asyncpg://${user}:${pass}@${host}:${port}/${slot_db}"
    local redis_url="redis://redis:6379/${TEST_SLOT}"

    log_info "Test slot ${TEST_SLOT}/${TEST_SLOT_COUNT}: db '${slot_db}', redis db ${TEST_SLOT}, container '${TEST_RUN_CONTAINER}' (isolated from dev + other runs)"
    # Background the run + `wait` on it so the cleanup trap fires *immediately* on
    # SIGTERM/SIGINT (TaskStop/Ctrl-C). `wait` is an interruptible builtin; a
    # foreground external command would instead defer the trap until pytest
    # finished if the signal hit only ctl.sh's PID (not the process group) —
    # exactly the 6a "killing bash leaves the container running" failure. `wait`
    # returns pytest's real exit code on a clean finish, which we propagate.
    local rc=0
    dc run --rm --name "$TEST_RUN_CONTAINER" --no-deps \
        -e APP_ENV=testing \
        -e POSTGRES_DB="${slot_db}" \
        -e DATABASE_URL="${test_url}" \
        -e REDIS_URL="${redis_url}" \
        api uv run --frozen pytest "$@" &
    TEST_RUN_PID=$!
    wait "$TEST_RUN_PID" || rc=$?
    return "$rc"
}

cmd_sandbox_build() { # Build the hardened code_exec sandbox image
    log_info "Building code_exec sandbox image (${SANDBOX_IMAGE}) from ops/sandbox/ ..."
    docker build "$@" -t "$SANDBOX_IMAGE" "$SANDBOX_DIR"
    log_success "Sandbox image built: ${SANDBOX_IMAGE}"
}

cmd_sandbox_test() {  # Prove the sandbox boundary holds (the escape battery)
    ensure_prod_off sandbox-test
    ensure_sandbox_image
    local fails=0 n=0

    # _sbx <name> <code> <expect-substr> [reject-substr] [timeout].
    # Code is passed as an ARG (not piped) so _sbx runs in THIS shell — a
    # `… | _sbx` pipeline would run it in a subshell and lose fails/n. Matching
    # is space-insensitive so it works whether the JSON came from the runner
    # (json.dumps, spaced) or the launcher's synthesised verdict (compact).
    _sbx() {
        local name="$1" code="$2" expect="$3" reject="${4:-}" to="${5:-10}" json flat ok=1
        n=$((n + 1))
        json="$(printf '%s' "$code" | SANDBOX_TIMEOUT="$to" "$SANDBOX_LAUNCHER")"
        flat="${json// /}"
        case "$flat" in *"$expect"*) ;; *) ok=0 ;; esac
        [ -n "$reject" ] && case "$flat" in *"$reject"*) ok=0 ;; esac
        if [ "$ok" -eq 1 ]; then
            log_success "[$n] ${name}"
        else
            log_error "[$n] ${name}"
            printf '       expect=%s reject=%s\n       got=%s\n' "$expect" "${reject:-—}" "$json" >&2
            fails=$((fails + 1))
        fi
    }

    log_info "Sandbox escape battery against ${SANDBOX_IMAGE} (assumes HOSTILE code; the container is the boundary)..."
    _sbx "compute works"              'print(2+2)'                                                       '"stdout":"4\n"'
    _sbx "runs as non-root"           'import os; print(os.getuid())'                                    '65534'            '"stdout":"0\n"'
    _sbx "sees only the container fs" 'print(open("/etc/passwd").read())'                                '"ok":true'        'dan:'
    _sbx "rootfs is read-only"        'open("/opt/escape","w").write("x")'                               '"ok":false'       '"ok":true'
    _sbx "/tmp is writable"           'open("/tmp/ok","w").write("hi"); print("wrote")'                  '"stdout":"wrote'
    _sbx "network is cut"             'import urllib.request as u; u.urlopen("http://1.1.1.1/",timeout=5)' '"ok":false'     '"ok":true'    15
    _sbx "infinite loop is killed"    $'while True:\n    pass'                                            '"timed_out":true' ''            3
    _sbx "memory cap enforced"        'x=bytearray(512*1024*1024); print(len(x))'                        '"ok":false'       '"stdout":"536870912'
    _sbx "thread/pid fan-out capped"  $'import threading,time\nfor _ in range(500):\n    threading.Thread(target=lambda: time.sleep(30),daemon=True).start()\nprint("started all")' \
                                                                                                         '"ok":false'       'startedall'

    echo
    if [ "$fails" -eq 0 ]; then
        log_success "Sandbox boundary holds: ${n}/${n} checks passed."
    else
        log_error "Sandbox boundary: ${fails}/${n} checks FAILED — DO NOT ship code_exec until fixed."
        return 1
    fi
}

cmd_health() {        # Report stack + dependency health
    dc ps
    log_info "Probing GET /api/health ..."
    if dc exec -T api python -c \
        "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8000/api/health',timeout=5).read().decode())" 2>/dev/null; then
        log_success "API healthy."
    else
        log_error "API not reachable — is the stack up? (./ctl.sh up)"
        return 1
    fi
}

cmd_reset() {         # Wipe ALL data (containers + volumes + bind dirs) — dev only
    ensure_prod_off reset
    confirm "This DESTROYS the dev database and redis data. Continue?" || exit 1
    dc down -v
    rm -rf "$SCRIPT_DIR/pgdata" "$SCRIPT_DIR/redisdata"
    log_success "All data wiped. Next 'up' starts fresh."
}

cmd_help() {
    cat <<EOF
$(printf '%bJohnny 5 — control script%b' "$BLUE" "$NC")

  ./ctl.sh <command> [args]

$(printf '%bLifecycle%b' "$GREEN" "$NC")
  up [svc...]        Build if needed and start the stack (waits for health)
  down [args]        Stop and remove containers (data kept)
  restart [svc]      Restart a service or the whole stack
  build [svc]        Build images
  rebuild [svc]      Rebuild --no-cache and recreate            ${DIM}(dev only)${NC}
  status             Show container + health status
  logs [svc]         Follow logs (last 120 lines)

$(printf '%bApp / data%b' "$GREEN" "$NC")
  migrate            Apply migrations (alembic upgrade head)
  test [pytest args] Run the suite in an isolated per-run slot   ${DIM}(dev only)${NC}
                     ${DIM}concurrent runs claim distinct DB+Redis slots (1..${TEST_SLOT_COUNT}); safe in parallel${NC}
  shell [svc]        Shell into a container (default: api)
  db                 Interactive psql into the dev database
  repl               Open the cockpit: watch him think, speak, pause/step
  health             Probe GET /api/health and show status
  reset              Wipe all data and start fresh              ${DIM}(dev only)${NC}

$(printf '%bcode_exec sandbox%b' "$GREEN" "$NC")
  sandbox-build      Build the hardened code_exec image (${SANDBOX_IMAGE})
  sandbox-test       Run the escape battery — prove the boundary holds ${DIM}(dev only)${NC}

  help               Show this message

$(printf '%bWeb UI%b' "$GREEN" "$NC")
  The 'web' service (nginx) serves the built SPA at https://${PUBLIC_DOMAIN:-johnny.demosrv.uk}/.
  'up' builds its image on first run; 'rebuild web' rebuilds after frontend changes.
  ${DIM}Live frontend dev (HMR): cd frontend && npm run dev — Vite proxies /api + /ws${NC}
  ${DIM}to the api container via the localhost:8000 port published in dev mode.${NC}

$(printf '%bMode:%b' "$YELLOW" "$NC") $([ "$PRODUCTION_MODE" = true ] && echo production || echo development)  ${DIM}(set via .production marker or PRODUCTION_MODE=true)${NC}
EOF
}

# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------
main() {
    local cmd="${1:-help}"; shift || true
    case "$cmd" in
        up)       cmd_up "$@" ;;
        down)     cmd_down "$@" ;;
        restart)  cmd_restart "$@" ;;
        build)    cmd_build "$@" ;;
        rebuild)  cmd_rebuild "$@" ;;
        status|ps) cmd_status "$@" ;;
        logs)     cmd_logs "$@" ;;
        migrate)  cmd_migrate "$@" ;;
        test)     cmd_test "$@" ;;
        sandbox-build) cmd_sandbox_build "$@" ;;
        sandbox-test)  cmd_sandbox_test "$@" ;;
        shell)    cmd_shell "$@" ;;
        db)       cmd_db "$@" ;;
        repl)     cmd_repl "$@" ;;
        health)   cmd_health "$@" ;;
        reset)    cmd_reset "$@" ;;
        help|-h|--help) cmd_help ;;
        *) log_error "Unknown command: $cmd"; echo; cmd_help; exit 1 ;;
    esac
}

main "$@"
