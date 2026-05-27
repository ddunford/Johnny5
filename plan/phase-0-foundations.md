# Phase 0: Foundations

## Overview
The substrate Johnny runs on. No cognition yet — this phase makes "the lights turn on": a booting FastAPI service, Postgres+pgvector and Redis up via `ctl.sh`, the LLM router talking to **real** Groq + `inference.lan` Qwen with circuit breakers, the embeddings + vision clients verified against live endpoints, structured logging, error tracking, a health endpoint, and CI. Everything later depends on this being solid, so it is verified end-to-end against live inference, not mocks.

**Done when:** `./ctl.sh up` brings the stack up healthy; `GET /api/health` reports all dependencies green; the router completes a real Groq call and a real Qwen call, and fails over correctly when one is forced down; embeddings return a 1024-d vector from TEI; CI passes on a PR.

## Custom Feature: Inference substrate (LLM router + clients)

**Purpose:** Every cognitive subsystem reaches inference only through this layer. No module covers our specific per-cognitive-role provider chain (Groq ⇄ local Qwen) with circuit breaking and graceful "tired" degradation. Borrows the `ai-llm` *pattern*, built in Python.

**Database tables:**
| Table | Key columns | Notes |
|-------|------------|-------|
| `llm_call_log` | id, ts, role, provider, model, prompt_tokens, completion_tokens, latency_ms, status, cost_usd | spend tracking + the `GROQ_DAILY_BUDGET_USD` ceiling; feeds "tired" degradation later |

**Internal interfaces (not HTTP — used by the Mind):**
- `LLMRouter.complete(role, messages, schema=None) -> Completion` — picks provider by role, enforces circuit breaker, retries-with-feedback on schema-validation failure, falls back down the chain, logs the call.
- `Embedder.embed(texts) -> list[Vector1024]` — calls `POST :8002/embed {"inputs"}` → `{"embeddings":[[...]]}` (custom server, NOT TEI native). 1024-d.
- `Vision.describe(image, prompt) -> str` — **`gemma4:e4b`** (multimodal, verified) via the router; `Detector.detect(image) -> list[Box]` — YOLO11 `:8003`.

**Service modules:**
- `brain/llm/router.py` — provider chain per role, circuit breaker, retry, logging.
- `brain/llm/providers/groq.py`, `brain/llm/providers/ollama.py` — adapters, OpenAI-compatible.
- `brain/llm/embeddings.py`, `brain/llm/vision.py` — TEI + Qwen-vision/YOLO clients.
- `core/governors.py` (stub this phase) — reads `llm_call_log` to enforce the daily budget; full enforcement Phase 6.

**Key patterns (non-obvious):**
- Model quirks are real and verified (`plan/inference-substrate.md`): **`gemma4:e4b`** returns clean `content` and is the primary local model; **`qwen3.5-9b-128k`** is a *thinking* model that returns empty `content` (reasoning in a separate channel) — its adapter must extract from the reasoning channel or it'll look broken. Do not assume `/no_think` fixes it.
- Circuit breaker per provider: open after 3–5 consecutive failures, auto-reset after 60s. When Groq's circuit is open, roles that default to Groq transparently fall to a local model ("tired") rather than erroring.
- Retry-with-feedback: on a schema-validation failure, re-prompt the same provider once with the validation error appended before failing over.
- Provider chain is **per role**, config-driven (e.g. `deliberation = [groq, qwen]`, `narrator = [qwen]`).

**Test checklist:** see `test-plan-phase-0.md`.

## Implementation steps
1. Scaffold the FastAPI service (`/fastapi-scaffold`): app package, settings via pydantic-settings reading `.env`, async startup/shutdown.
2. Docker: multi-stage `Dockerfile`, `docker-compose.yml` (api, postgres+pgvector, redis), Traefik labels (`traefik_demosrv`, `le`, host `johnny.demosrv.uk`), `.env`/`.env.testing`.
3. `ctl.sh` with standard commands (`up`, `down`, `logs`, `migrate`, `test`, `shell`, `health`, `help`) — dev/prod aware, health checks, safety confirms.
4. Postgres: enable `pgvector` extension; Alembic migration baseline + `llm_call_log` table.
5. Redis: connection helper + health ping.
6. LLM router + provider adapters (Groq, Ollama), circuit breaker, retry-with-feedback, call logging.
7. Embeddings (TEI) + vision (Qwen + YOLO) clients.
8. Structured JSON logging (correlation IDs) + Sentry wired to the FastAPI exception handler.
9. `GET /api/health` — reports Postgres, Redis, Groq, Qwen, TEI, YOLO status.
10. CI: GitHub Actions — lint (ruff), type-check (mypy), test (pytest) on PR.
11. Verify the credential-guard hook is active and `.env.example` covers every new var.

## Tasks

- [x] `TASK-0.1` Scaffold FastAPI service (app package, pydantic-settings, async lifespan) and verify it boots → `/fastapi-engineer`
- [x] `TASK-0.2` ⫘ Docker: multi-stage Dockerfile + docker-compose (api, postgres+pgvector, redis) + Traefik labels + `.env`/`.env.testing` → `/devops-deployment-engineer`
- [x] `TASK-0.3` ⫘ Write `ctl.sh` (up/down/logs/migrate/test/shell/health/help; dev/prod aware) → `/devops-deployment-engineer`
- [x] `TASK-0.4` Alembic baseline + enable pgvector + `llm_call_log` migration → `/fastapi-engineer`
- [x] `TASK-0.5` Redis connection helper + ping; Postgres async session/repository base → `/fastapi-engineer`
- [x] `TASK-0.6` Implement LLM router: per-role provider chain, circuit breaker, retry-with-feedback, call logging → `/fastapi-engineer` [TC-0.3, TC-0.4]
- [x] `TASK-0.7` ⫘ Groq + Ollama provider adapters (OpenAI-compatible; gemma4 clean `content`; qwen3.5-9b reasoning-channel handling) → `/fastapi-engineer` [TC-0.3]
- [x] `TASK-0.8` ⫘ Embeddings client (`:8002 /embed` custom contract) + vision (gemma4:e4b) + YOLO clients → `/fastapi-engineer` [TC-0.5]
- [x] `TASK-0.9` Stub `core/governors.py` reading `llm_call_log` for daily budget (enforcement deferred to Phase 6) → `/fastapi-engineer`
- [x] `TASK-0.10` ⫘ Structured JSON logging + correlation IDs + Sentry on the exception handler → `/observability-engineer`
- [x] `TASK-0.11` `GET /api/health` reporting all six dependencies → `/fastapi-engineer` [TC-0.1, TC-0.2]
- [x] `TASK-0.12` ⫘ CI workflow: ruff + mypy + pytest on PR → `/devops-deployment-engineer` [TC-0.7]
- [x] `TASK-0.13` Contract tests for Groq + Qwen adapters (response-envelope fixtures → projection) → `/qa-test-engineer` [TC-0.3]
- [x] `TASK-0.14` Router resilience tests: circuit opens, fails over to local, recovers (frozen-clock) → `/qa-test-engineer` [TC-0.4]
- [ ] `TASK-0.15` Live verification against real `inference.lan` + Groq: real completion both providers, real 1024-d embedding, forced-failover smoke → `/qa-test-engineer` [TC-0.3, TC-0.4, TC-0.5]
- [ ] `TASK-0.16` ⫘ OWASP/secrets review: `.env` handling, no creds in logs/Sentry, hook active, health endpoint not leaking internals → `/security-reviewer` [TC-0.6]

## Notes
- This phase ships no cognition and no UI — verification is via `ctl.sh`, the health endpoint, and pytest. No Playwright yet (no pages).
- "Verify external access before writing clients" (global rule 6): TASK-0.15 must pass against live endpoints before Phase 1 starts. If `inference.lan` or Groq is unreachable/unconfigured, mark dependent work `[BLOCKED]` — do not mock past it.
