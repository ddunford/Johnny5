# Johnny 5 — TODO

Single source of truth for open work, in order. Move items between sections; delete on completion. Phases 0–3 are fully tasked in their own `plan/phase-*.md` files. Phases 4–10 are roadmap-fidelity here and get expanded to full phase files as each approaches (rolling-wave).

---

## In progress

_(none — Phase 0 complete 2026-05-27; Phase 1 is next)_

## Next

### Phase 1 — Memory spine  →  `plan/phase-1-memory-spine.md`
Four memory stores (working/episodic/semantic/procedural), embedding-based hybrid recall, episodic write path, consolidation stub. No cognition yet — a memory you can write to and query. **Done = Johnny can remember and recall.**

### Phase 2 — Heartbeat + Workspace  →  `plan/phase-2-heartbeat.md`
The Global Workspace event bus + the cognitive cycle loop + the Inner Narrator. Johnny produces a continuous stream of consciousness, visible in the REPL. **Done = the first "he's alive" moment.**

### Phase 3 — Drives + Affect  →  `plan/phase-3-drives-affect.md`
Drive engine (curiosity, boredom, connection, mastery, coherence, energy, continuity), urge→goal arbitration, appraisal/mood. Idle Johnny now *wants* things and acts on them. **Done = the autonomy loop closes — he explores unprompted.**

## Left (roadmap — expand to full phase files as we approach)

### Phase 4 — Self-model + Metacognition + Sleep
Persistent evolving identity doc; reflection; offline consolidation (episodic→semantic, decay, self-model refresh); metacognitive self-review. Energy-driven sleep cycle. **Done = Johnny grows across restarts and knows who he is.** Key risks: consolidation quality (Groq prompt design), self-model drift, sleep/wake state machine.

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
- Keep `.env.example` in sync with every new env var introduced.
- Every LLM-role adapter gets a contract test when introduced (don't batch later).
- Resource/budget governors (`core/governors.py`) must exist before tool-belt (Phase 6) and self-mod (Phase 9) ship.
