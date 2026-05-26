# Test Plan: Phase 0 — Foundations

## Prerequisites
- `.env` populated (Groq key, Postgres creds); `inference.lan` reachable on the LAN.
- `./ctl.sh up` run; migrations applied (`./ctl.sh migrate`).

## Test Cases

### TC-0.1: Stack boots healthy
**Steps:** `./ctl.sh up`; wait for healthy; `curl -s localhost/api/health`.
**Expected:** 200; JSON shows `postgres`, `redis` = ok. Containers report healthy in `docker ps`.
**Status:** ⬜

### TC-0.2: Health reports a downed dependency
**Steps:** Stop Redis (`docker stop johnny5-redis`); hit `/api/health`.
**Expected:** Endpoint still responds (does not hang); `redis` = down, overall status degraded. Restart restores green.
**Status:** ⬜

### TC-0.3: Router completes real calls on both providers
**Steps:** Invoke router with role=`deliberation` (Groq) and role=`narrator` (Qwen) via a test harness/REPL.
**Expected:** Both return non-empty content. Qwen call used `/no_think` and `content` is populated (not empty/reasoning-only). `llm_call_log` rows written with token counts + latency.
**Status:** ⬜

### TC-0.4: Circuit breaker + failover
**Steps:** Point Groq base URL at an unreachable host (or inject failures); make 5 `deliberation` calls; then restore.
**Expected:** After threshold the Groq circuit opens; subsequent `deliberation` calls transparently served by local Qwen ("tired"), no exception bubbles to caller. After 60s the circuit half-opens and recovers. Frozen-clock unit test asserts the timing deterministically.
**Status:** ⬜

### TC-0.5: Embeddings + vision live
**Steps:** Embed a short string via TEI; describe a test image via Qwen vision; detect objects via YOLO.
**Expected:** Embedding is a 1024-float vector. Vision returns a plausible caption. YOLO returns boxes/labels. All within timeout.
**Status:** ⬜

### TC-0.6: Secrets hygiene
**Steps:** Attempt to commit a file containing `GROQ_API_KEY=gsk_...`; inspect logs/Sentry payloads for the key.
**Expected:** Pre-commit hook blocks the commit. No API key or DB password appears in structured logs or Sentry events.
**Status:** ⬜

### TC-0.7: CI gate
**Steps:** Open a PR with a deliberate ruff/mypy violation.
**Expected:** CI fails on lint/type-check; cannot merge until fixed.
**Status:** ⬜
