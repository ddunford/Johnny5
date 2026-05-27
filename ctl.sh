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

# Create the dedicated test database on the running postgres if it is missing.
# Uses the dev superuser over the local socket (trust) — the test DB lives on
# the same server but is a separate database, so dev data is never touched.
TEST_DB="johnny5_test"
ensure_test_db() {
    local user="${POSTGRES_USER:-johnny5}"
    if dc exec -T postgres psql -U "$user" -tAc \
        "SELECT 1 FROM pg_database WHERE datname='${TEST_DB}'" 2>/dev/null | grep -q 1; then
        return 0
    fi
    log_info "Creating test database '${TEST_DB}'..."
    dc exec -T postgres createdb -U "$user" -O "$user" "${TEST_DB}"
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
    log_success "Build complete."
}

cmd_rebuild() {       # Rebuild images from scratch and recreate (dev only)
    ensure_prod_off rebuild
    dc build --no-cache "$@"
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

cmd_test() {          # Run the test suite against johnny5_test (NEVER the dev DB)
    ensure_prod_off test
    log_info "Starting datastores for tests..."
    dc up -d postgres redis
    wait_for_healthy postgres
    ensure_test_db
    local user="${POSTGRES_USER:-johnny5}" pass="${POSTGRES_PASSWORD:-}"
    local host="${POSTGRES_HOST:-postgres}" port="${POSTGRES_PORT:-5432}"
    local test_url="postgresql+asyncpg://${user}:${pass}@${host}:${port}/${TEST_DB}"
    log_info "pytest -> database '${TEST_DB}' (isolated from dev)"
    dc run --rm --no-deps \
        -e APP_ENV=testing \
        -e POSTGRES_DB="${TEST_DB}" \
        -e DATABASE_URL="${test_url}" \
        -e REDIS_URL="${TEST_REDIS_URL:-redis://redis:6379/1}" \
        api uv run --frozen pytest "$@"
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
  test [pytest args] Run the suite against johnny5_test         ${DIM}(dev only)${NC}
  shell [svc]        Shell into a container (default: api)
  db                 Interactive psql into the dev database
  repl               Open the cockpit: watch him think, speak, pause/step
  health             Probe GET /api/health and show status
  reset              Wipe all data and start fresh              ${DIM}(dev only)${NC}

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
