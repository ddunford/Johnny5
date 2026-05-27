# Johnny 5 — TODO

Single source of truth for open work, in order. Move items between sections; delete on completion. Phases 0–3 are fully tasked in their own `plan/phase-*.md` files. Phases 4–10 are roadmap-fidelity here and get expanded to full phase files as each approaches (rolling-wave).

---

## In progress

## Next

### Phase 6 — Tool belt + Conscience  →  split into 6a + 6b (tasked, ready)
He acts on the world, safely. Split into sub-phases because the safe-action substrate (Conscience + vetted dispatch + audit + the carried safety items) MUST land + be reviewable before any world-touching tool.

- **Phase 6a — Safe-action substrate** → `plan/phase-6a-safe-action-substrate.md` (11 tasks, backend + lead-security). Fills the cycle CHECK stub with the **Conscience** (values vetting, in the Mind — FC-9) + makes ACT run an approved tool via a typed `ToolRegistry`, with the single FC-5 dispatch point now writing an append-only `action_log` (Core `audit.py`, FC-1). Lands the **three carried advisories**: wire `BudgetGovernor` into `LLMRouter.complete()` as a **hard pre-call gate** (P3 — then revert `deliberation` to cloud-first); **`/no_think`** for the reasoning model on schema roles (P4 — reliable qwen fallback); **no-secrets-on-the-bus** redaction (P5a). One inert tool only — no world-touching yet.
- **Phase 6b — Tool belt** → `plan/phase-6b-tool-belt.md` (13 tasks, backend + devops + qa + lead-security). The tools on 6a's vetted substrate: `web_search`/`web_fetch`(**SSRF-hardened**)/`news` (the primary curiosity feed → consolidated into memory), `code_exec` (**escape-resistant sandbox container**), `note`, `schedule_wakeup`, `memory_search/write`; Deliberation extended to pick external tools. **The curiosity loop goes live: idle → reads the world → remembers → drive eased.**

**Done = he can act on the world, safely.** Run `/plan-review phase 6a` then `/team-execute` (6a, then 6b). **Out of scope (later phases):** messaging/outward-contact (P8), self-ops + self-code edits (P9), social presence (post-v1).

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
- **[Phase 3 security advisory — MEDIUM, deferred to Phase 6]** The `BudgetGovernor` exists but is **never consulted before an LLM call** — the router logs cost but doesn't gate on spend, so the `$5/day` cap can't actually halt an autonomous paid loop. Phase 3 mitigated by routing `deliberation` local-first (autonomous loop is $0-marginal). **Phase 6 must wire `BudgetGovernor.over_budget()` into `LLMRouter.complete()` as a hard pre-call gate** (skip a cloud step / degrade to local when exhausted) — then `deliberation` can return to cloud-first safely. Cite this when expanding `plan/phase-6-*.md`.
- **[Phase 5a advisory → Phase 6+]** The `/api/v1/audit` endpoint returns `workspace_event` payloads verbatim to any token-holder. Today no bus event carries a secret (cognition data only), but **Phase 6 (tools/web/messaging) + Phase 8 must never put secrets — API responses, tokens, fetched-page auth — into a `workspace_event` payload**, or `/audit` leaks them. Keep secrets out of the bus.
- **[Phase 5a advisory → Phase 5b + later]** The browser WebSocket connects with `?token=` in the URL (the WS API can't set custom headers), so the shared token appears in the WS URL — proxy/access logs could capture it. Interim-gate limitation: mitigated by `wss://` (encrypted on the wire) + the single-user scope; the real fix is the session/cookie auth gate (a later phase) replacing the shared-token model behind the same dependency. Note in `plan/phase-5b-web-ui.md` TASK-5b.15.
- **[Phase 4 finding → Phase 6 inference-substrate]** The local reasoning model (qwen3.5) is **unreliable for STRUCTURED (JSON) output** — its chain-of-thought ramble before the JSON is non-deterministic and intermittently blows even a 4096-token ceiling (reproduced sole-user; ~154-187s/call), and a worst-case ceiling would blow the 240s reasoning timeout. The 3 cloud-first sleep roles (consolidation/self_model/metacognition) therefore degrade to their deterministic fallbacks when Groq is down (by-design "tired" degradation, SPEC §10 — sleep still completes + wakes). To make the **local fallback actually grow** Johnny when Groq is down, Phase 6 should wire **`/no_think` (ollama `think:false` option)** for the reasoning model on schema/structured roles so qwen emits JSON directly — the prompt-token form does NOT work for this tag (verified). Details in `plan/inference-substrate.md`. Until then the `@live` token-budget guards point at the Groq primary path, and the qwen fallback is covered by a deterministic graceful-degradation test.
- **[Phase 3 test-infra note → backlog]** `./ctl.sh test` always targets the single `johnny5_test` DB (Redis db 1) with no concurrency guard, so two simultaneous runs corrupt each other (interleaved TRUNCATEs → `IntegrityError` masquerading as a regression — see `lessons.md`). Until fixed, coordinate one runner at a time. Real fix: a `flock` guard in `ctl.sh test` (refuse/queue a second run) **or** per-run DB+Redis isolation — the proven pattern is overriding `POSTGRES_DB`/`DATABASE_URL`/`REDIS_URL` to a `_be`-suffixed DB + a distinct Redis db (as used to verify Phase 3 concurrently). Wire that into `ctl.sh test` so parallel runs can't collide.
