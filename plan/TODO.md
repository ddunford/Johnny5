# Johnny 5 — TODO

Single source of truth for open work, in order. Move items between sections; delete on completion. Phases 0–3 are fully tasked in their own `plan/phase-*.md` files. Phases 4–10 are roadmap-fidelity here and get expanded to full phase files as each approaches (rolling-wave).

---

## In progress

## Next

### Phase 5 — Web UI (`johnny.demosrv.uk`)  →  split into 5a + 5b (tasked, ready)
The browser: watch and talk to him. Split into sub-phases because it spans a cohesive backend-API layer + a React SPA, and the frontend↔backend contract is the load-bearing risk (the `/api/v1` seam is empty today; there's no HTTP way to talk to him yet).

- **Phase 5a — Web API** → `plan/phase-5a-web-api.md` (backend, 10 tasks). The read/input/auth doorway the UI consumes: `POST /api/v1/input` (push to the Sensorium `InputQueue` — the "talk to him" send; reply streams on `/ws/consciousness`), GET endpoints (state snapshot, thoughts, memory episodes+facts, goals, audit/bus log incl. dispatched actions, sleeps, self), a shared-token HTTP gate on `/api/v1` + `/api/health` topology redaction (the carried Phase-0 advisory), and **captured wire fixtures** (populated + empty-state) for 5b contract pinning. Run 5a first.
- **Phase 5b — Web UI** → `plan/phase-5b-web-ui.md` (frontend + devops + qa, 15 tasks). React 19 + Vite + TS SPA behind Traefik (new `web` nginx service, path-routed): Conversation, live Consciousness stream, State dashboard (drives/mood/goals/energy + awake-asleep/⚠DEGRADED/self-model-version/last-sleep), Memory browser, Audit, read-only Self panel. Service-layer **contract tests fed 5a's captured wire fixtures** + a mandatory **fresh-load smoke** against a real backend (`/plan-review` 7b/7c). Token gate driven from the UI. (Self-edit *approval* UI is Phase 9 — labelled placeholder only.)

**Done = you can watch and talk to him in a browser.** Run `/plan-review` then `/team-execute` (5a, then 5b).

## Left (roadmap — expand to full phase files as we approach)

### Phase 6 — Tool belt + Conscience
Effectors + tools: web search/fetch, **news browsing** (primary curiosity feed), sandboxed code execution, notes/journal, self-scheduler, memory ops. The Mind's Conscience (values vetting) + the Core's integrity check + audit. **Done = he can act on the world, safely.**

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
- **[Phase 4 finding → Phase 6 inference-substrate]** The local reasoning model (qwen3.5) is **unreliable for STRUCTURED (JSON) output** — its chain-of-thought ramble before the JSON is non-deterministic and intermittently blows even a 4096-token ceiling (reproduced sole-user; ~154-187s/call), and a worst-case ceiling would blow the 240s reasoning timeout. The 3 cloud-first sleep roles (consolidation/self_model/metacognition) therefore degrade to their deterministic fallbacks when Groq is down (by-design "tired" degradation, SPEC §10 — sleep still completes + wakes). To make the **local fallback actually grow** Johnny when Groq is down, Phase 6 should wire **`/no_think` (ollama `think:false` option)** for the reasoning model on schema/structured roles so qwen emits JSON directly — the prompt-token form does NOT work for this tag (verified). Details in `plan/inference-substrate.md`. Until then the `@live` token-budget guards point at the Groq primary path, and the qwen fallback is covered by a deterministic graceful-degradation test.
- **[Phase 3 test-infra note → backlog]** `./ctl.sh test` always targets the single `johnny5_test` DB (Redis db 1) with no concurrency guard, so two simultaneous runs corrupt each other (interleaved TRUNCATEs → `IntegrityError` masquerading as a regression — see `lessons.md`). Until fixed, coordinate one runner at a time. Real fix: a `flock` guard in `ctl.sh test` (refuse/queue a second run) **or** per-run DB+Redis isolation — the proven pattern is overriding `POSTGRES_DB`/`DATABASE_URL`/`REDIS_URL` to a `_be`-suffixed DB + a distinct Redis db (as used to verify Phase 3 concurrently). Wire that into `ctl.sh test` so parallel runs can't collide.
