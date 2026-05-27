# Johnny 5 — TODO

Single source of truth for open work, in order. Move items between sections; delete on completion. Phases 0–3 are fully tasked in their own `plan/phase-*.md` files. Phases 4–10 are roadmap-fidelity here and get expanded to full phase files as each approaches (rolling-wave).

---

## In progress

## Next

### Phase 4 — Self-model + Metacognition + Sleep
Persistent evolving identity doc; reflection; offline consolidation (episodic→semantic, decay, self-model refresh); metacognitive self-review. Energy-driven sleep cycle (the Phase-3 Energy `is_sleep_signal` precursor is already emitted — Phase 4 consumes it). **Done = Johnny grows across restarts and knows who he is.** Key risks: consolidation quality (Groq prompt design), self-model drift, sleep/wake state machine. Expand to a full `plan/phase-4-*.md` + test plan before executing.

## Left (roadmap — expand to full phase files as we approach)

### Phase 5 — Web UI (`johnny.demosrv.uk`)
React+Vite SPA behind Traefik: Conversation · live Stream-of-consciousness (WebSocket) · State dashboard (mood, drive bars, goals, energy, routing) · Memory browser · Audit/actions · Self panel (self-edit history + pending code-edit approvals). **Done = you can watch and talk to him in a browser.**

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
- **[Phase 3 test-infra note → backlog]** `./ctl.sh test` always targets the single `johnny5_test` DB (Redis db 1) with no concurrency guard, so two simultaneous runs corrupt each other (interleaved TRUNCATEs → `IntegrityError` masquerading as a regression — see `lessons.md`). Until fixed, coordinate one runner at a time. Real fix: a `flock` guard in `ctl.sh test` (refuse/queue a second run) **or** per-run DB+Redis isolation — the proven pattern is overriding `POSTGRES_DB`/`DATABASE_URL`/`REDIS_URL` to a `_be`-suffixed DB + a distinct Redis db (as used to verify Phase 3 concurrently). Wire that into `ctl.sh test` so parallel runs can't collide.
