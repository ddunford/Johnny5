# syntax=docker/dockerfile:1.9
#
# Johnny 5 — application image (FastAPI host: johnny.main:app).
#
# uv-managed, multi-stage. Dependency resolution is cached on pyproject.toml +
# uv.lock so source edits don't re-resolve. Two final targets:
#   - `development` — includes dev tools (ruff/mypy/pytest), runs uvicorn with
#     --reload. Used by docker-compose.override.yml (auto-loaded in dev).
#   - `runtime`     — lean, non-root, no dev deps. The production default.
#
# Build a stage explicitly with `--target`; compose selects per environment.

ARG PYTHON_VERSION=3.12
ARG UV_VERSION=0.5

# ---------------------------------------------------------------------------
# uv binary (pinned) — copied into the build stages below.
# ---------------------------------------------------------------------------
FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

# ---------------------------------------------------------------------------
# dockercli — the Docker CLI binary only (client, no daemon). The code_exec
# launcher uses it to talk to the SCOPED docker-socket-proxy via $DOCKER_HOST.
# Our images never mount the raw socket — least privilege (see ops/sandbox/).
# ---------------------------------------------------------------------------
FROM docker:27-cli AS dockercli

# ---------------------------------------------------------------------------
# base — Python + uv + shared environment. Source of every other stage.
# ---------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim-bookworm AS base
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:$PATH"
COPY --from=uv /uv /uvx /bin/
WORKDIR /app

# ---------------------------------------------------------------------------
# builder — production deps (no dev group), then install the project.
# Step 1 (deps) is a cached layer keyed on pyproject.toml + uv.lock only.
# ---------------------------------------------------------------------------
FROM base AS builder
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    uv sync --frozen --no-install-project --no-dev
COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# ---------------------------------------------------------------------------
# builder-dev — same as builder but WITH the dev group (ruff/mypy/pytest).
# ---------------------------------------------------------------------------
FROM base AS builder-dev
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    uv sync --frozen --no-install-project
COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen

# ---------------------------------------------------------------------------
# development — dev image; hot-reload. Compose mounts live source over /app
# and masks /app/.venv with an anonymous volume so this prebuilt env persists.
# ---------------------------------------------------------------------------
FROM builder-dev AS development
ENV APP_ENV=development
# Docker CLI (client only) for the code_exec launcher → docker-socket-proxy.
COPY --from=dockercli /usr/local/bin/docker /usr/local/bin/docker
EXPOSE 8000
CMD ["uvicorn", "johnny.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# ---------------------------------------------------------------------------
# runtime — lean production image, non-root. Carries uv + source so the
# control script can run `uv run --frozen alembic upgrade head` offline.
# ---------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim-bookworm AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_PYTHON_DOWNLOADS=never \
    HOME=/app \
    PATH="/app/.venv/bin:$PATH"
COPY --from=uv /uv /uvx /bin/
# Docker CLI (client only) for the code_exec launcher → docker-socket-proxy.
COPY --from=dockercli /usr/local/bin/docker /usr/local/bin/docker
RUN groupadd --system --gid 10001 johnny \
 && useradd --system --uid 10001 --gid johnny --home-dir /app --no-create-home johnny
WORKDIR /app
COPY --from=builder --chown=johnny:johnny /app /app
USER johnny
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)"]
CMD ["uvicorn", "johnny.main:app", "--host", "0.0.0.0", "--port", "8000"]
