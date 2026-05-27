# Johnny 5 — TODO

Single source of truth for open work, in order. Move items between sections; delete on completion. Phases 0–3 are fully tasked in their own `plan/phase-*.md` files. Phases 4–10 are roadmap-fidelity here and get expanded to full phase files as each approaches (rolling-wave).

---

## In progress

_(none — Phase 6a complete; 6b is next)_

## Next

### Phase 6b — Tool belt (the curiosity loop) → `plan/phase-6b-tool-belt.md` (15 tasks, backend + devops + frontend + qa + lead-security)
The world-touching tools on 6a's now-shipped vetted substrate (Conscience CHECK + `EffectorDispatch` ACT + append-only `action_log` + budget hard-gate + redaction — all live): `web_search`/`web_fetch`(**SSRF-hardened**)/`news` (the primary curiosity feed → consolidated into memory), `code_exec` (**escape-resistant sandbox container**), `note`, `schedule_wakeup`, `memory_search/write`; Deliberation extended to pick external tools; AuditPanel renders the durable `action_log` trail (6b.14 — backend read already shipped in 6a). **The curiosity loop goes live: idle → reads the world → remembers → drive eased.** Carries 2 LOW advisories from the 6a security review (Conscience prompt-injection from fetched content; redaction of novel secret shapes) into `TASK-6b.13`.

**Done = he can act on the world, safely.** Run `/plan-review phase 6b` then `/team-execute phase 6b`. **Out of scope (later phases):** messaging/outward-contact (P8), self-ops + self-code edits (P9), social presence (post-v1).

> **Phase 6a — Safe-action substrate: ✅ COMPLETE** (`plan/phase-6a-safe-action-substrate.md`, all 12 tasks). Conscience (pure editable values, FC-9) fills CHECK; vetted `EffectorDispatch` runs tools at ACT; append-only Core `action_log` (FC-1) + durable `GET /api/v1/audit/actions` read; `BudgetGovernor` hard pre-call gate (P3 resolved, deliberation back to cloud-first); qwen `/no_think` via native `/api/chat` (P4 resolved); no-secrets-on-bus redaction both paths (P5a resolved). Security review PASS; full suite 325×3 deterministic + @live + contract test green.

## Left (roadmap — expand to full phase files as we approach)

### Phase 7 — Voice (always-on)
Wake-word (openWakeWord) → **Speaches STT** (`:8890`, `Systran/faster-whisper-small`) → percept; **Kokoro TTS** (`:8880`) → **Johnny robot-voice DSP** out (see `voice/` — PoC built); unprompted speech driven by affect/drives; barge-in. CPU TTS/STT latency is significant → stream/queue, never block the cycle. **Done = you can talk to him out loud and he talks back, unprompted, in his own voice.**

### Phase 8 — Push / messaging
Outbound contact (push/Slack/Gmail) when Connection drive is high or he wants to share/needs approval. Rate-limited by the Social Model. **Done = he reaches out to you on his own.**

### Phase 9 — Self-modification
Runtime prompt/drive/agent editing (tiers 1–2, git-versioned, auto-checkpoint) + git-backed self-code propose→sandbox→approve flow (tier 3, Core-enforced) + Self panel wiring. **Done = he can safely rewrite parts of his own mind.**

### Phase 10 — HAL
Finalise the sensor/actuator abstraction; mock hardware adapters; documented contract so a Pi/Jetson robot body can attach with zero core changes. **Done = the brain is body-ready.** (Robot build itself is post-v1.)

---

## Cross-cutting / not phase-bound
- **[Phase 0 security advisory — LOW]** `/api/health` exposes per-dependency up/down + latency to any unauthenticated caller. Fine now (LAN-internal, single-user). When the public web UI / auth gate lands (**Phase 5**), auth-gate `/api/health` or return a bare 200/503 to unauthenticated callers (avoid infra-topology disclosure).
- **[Phase 0 test-infra note → Phase 1]** `foundation.db` uses a process-global async engine (correct for production's single uvicorn loop). In async tests spanning multiple event loops, don't reuse it across loops — use a per-test/short-lived engine (see the in-memory-call-logger pattern in `tests/llm/test_live_inference.py`). Matters for **Phase 1** (memory spine = heavy async DB tests). Optional product hardening: health check could use a short-lived connection.
- Keep `.env.example` in sync with every new env var introduced.
- Every LLM-role adapter gets a contract test when introduced (don't batch later).
- Resource/budget governors (`core/governors.py`) must exist before tool-belt (Phase 6) and self-mod (Phase 9) ship.
- ✅ **[Phase 3 — BudgetGovernor hard gate] RESOLVED in 6a** (TASK-6a.6): the gate is wired into `LLMRouter.complete()` (skip paid step → degrade to local when exhausted; UTC-reset proven); `deliberation` is back to cloud-first.
- ✅ **[Phase 5a — no-secrets-on-the-bus] RESOLVED in 6a** (TASK-6a.8): `foundation/redaction.py` scrubs both write paths (`workspace_event` + `action_log`) before persistence. **Phase 8 (messaging) must still route any new secret-bearing payload through the same guard** — the mechanism exists, keep using it.
- **[Phase 5a advisory → Phase 5b + later]** The browser WebSocket connects with `?token=` in the URL (the WS API can't set custom headers), so the shared token appears in the WS URL — proxy/access logs could capture it. Interim-gate limitation: mitigated by `wss://` (encrypted on the wire) + the single-user scope; the real fix is the session/cookie auth gate (a later phase) replacing the shared-token model behind the same dependency. Note in `plan/phase-5b-web-ui.md` TASK-5b.15.
- ✅ **[Phase 4 — qwen `/no_think`] RESOLVED in 6a** (TASK-6a.7): the working mechanism is the native `/api/chat` with `think:false` + `format:json` (the `/v1` `think` field is ignored) — `OllamaProvider` routes the reasoning model on schema roles there; proven by `tests/llm/test_no_think_live.py` (flat + nested schema). Verified details in `plan/inference-substrate.md`.
- **[Phase 3 test-infra → DO before Phase 6b, RAISED PRIORITY]** `./ctl.sh test` targets the single `johnny5_test` DB with no concurrency guard, so two simultaneous runs corrupt each other (interleaved TRUNCATEs → `IntegrityError` masquerading as a regression — see `lessons.md`). **This actively bit us in 6a**: a stray lead `./ctl.sh test` left a detached container that corrupted a concurrent run (17 phantom failures; cost a full re-run + diagnosis). Coordinating one-runner-at-a-time is fragile under agent teams. Real fix before 6b's heavy test load: a `flock` guard in `ctl.sh test` (refuse/queue a second run) **or** per-run DB+Redis isolation (override `POSTGRES_DB`/`DATABASE_URL`/`REDIS_URL` to a `_be`-suffixed DB + distinct Redis db). Worth a small infra task at the top of 6b.
