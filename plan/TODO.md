# Johnny 5 — TODO

Single source of truth for open work, in order. Move items between sections; delete on completion. Phases 0–3 are fully tasked in their own `plan/phase-*.md` files. Phases 4–10 are roadmap-fidelity here and get expanded to full phase files as each approaches (rolling-wave).

---

## In progress

_(none — Phase 6b complete + deployed)_

## Next

**Phase 7 — Voice (always-on)** — the next phase; see the roadmap entry under *Left*. Expand to a full `plan/phase-7-*.md` (re-run `/bootstrap-from-spec` scope or hand-write) when approached.

> **Phase 6b — Tool belt (the curiosity loop): ✅ COMPLETE + DEPLOYED** (`plan/phase-6b-tool-belt.md`, all 16 tasks). All 9 tools on 6a's vetted substrate (web_search/web_fetch[SSRF-hardened]/news/code_exec[scoped-proxy sandbox]/note/schedule_wakeup/memory_search/write); Deliberation maps drives→tools; the **curiosity loop is live** — deployed, and Johnny autonomously read news on his own (idle→read→remember→eased). AuditPanel shows the durable action trail. Security review PASS (SSRF @live, sandbox escape 9/9, 2 LOW residuals → hardening backlog above). Full suite 508×3 deterministic. Deployed to the dev stack (Johnny woke intact: thoughts 1452→1480). **Out of scope (later):** messaging (P8), self-ops/self-code (P9), social presence (post-v1).

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
- ✅ **[Phase 6b security review — PASS, no Critical/High]** SSRF (`web_fetch`) verified @live (real DNS blocks loopback/private/link-local/metadata/ULA/`inference.lan`/non-http(s)/embedded-creds; public validates) + IP-pinned (DNS-rebind-proof) + per-hop re-validation; sandbox escape battery 9/9 + the docker-socket-proxy scoped (exec/info/volume/network → 403, raw socket only on the proxy); no tool reaches `tool.run` outside the vetted dispatch; all LLM calls (incl. the web-read consolidator) gated by the BudgetGovernor. **2 LOW residuals carried forward (security-hardening backlog, not phase-bound):** (a) the Conscience prompt has no explicit injection-resistance framing — LOW in 6b because fetched web *content* goes to the consolidator→memory, not into a vetted action's args/`goal_description` (only an indirect content→fact→future-goal path exists, and it's summarised) — harden the prompt if/when external content can reach a vet more directly (P8 messaging / social); (b) redaction is best-effort on *novel* secret shapes (known config values + gsk_/sk-/AKIA/JWT/Bearer + sensitive key-names are caught; an unrecognised-format secret from a fetched page could slip) — consider entropy/format expansion if Johnny ever handles third-party credentials.
- **[Phase 0 security advisory — LOW]** `/api/health` exposes per-dependency up/down + latency to any unauthenticated caller. Fine now (LAN-internal, single-user). When the public web UI / auth gate lands (**Phase 5**), auth-gate `/api/health` or return a bare 200/503 to unauthenticated callers (avoid infra-topology disclosure).
- **[Phase 0 test-infra note → Phase 1]** `foundation.db` uses a process-global async engine (correct for production's single uvicorn loop). In async tests spanning multiple event loops, don't reuse it across loops — use a per-test/short-lived engine (see the in-memory-call-logger pattern in `tests/llm/test_live_inference.py`). Matters for **Phase 1** (memory spine = heavy async DB tests). Optional product hardening: health check could use a short-lived connection.
- Keep `.env.example` in sync with every new env var introduced.
- Every LLM-role adapter gets a contract test when introduced (don't batch later).
- Resource/budget governors (`core/governors.py`) must exist before tool-belt (Phase 6) and self-mod (Phase 9) ship.
- ✅ **[Phase 3 — BudgetGovernor hard gate] RESOLVED in 6a** (TASK-6a.6): the gate is wired into `LLMRouter.complete()` (skip paid step → degrade to local when exhausted; UTC-reset proven); `deliberation` is back to cloud-first.
- ✅ **[Phase 5a — no-secrets-on-the-bus] RESOLVED in 6a** (TASK-6a.8): `foundation/redaction.py` scrubs both write paths (`workspace_event` + `action_log`) before persistence. **Phase 8 (messaging) must still route any new secret-bearing payload through the same guard** — the mechanism exists, keep using it.
- **[Phase 5a advisory → Phase 5b + later]** The browser WebSocket connects with `?token=` in the URL (the WS API can't set custom headers), so the shared token appears in the WS URL — proxy/access logs could capture it. Interim-gate limitation: mitigated by `wss://` (encrypted on the wire) + the single-user scope; the real fix is the session/cookie auth gate (a later phase) replacing the shared-token model behind the same dependency. Note in `plan/phase-5b-web-ui.md` TASK-5b.15.
- ✅ **[Phase 4 — qwen `/no_think`] RESOLVED in 6a** (TASK-6a.7): the working mechanism is the native `/api/chat` with `think:false` + `format:json` (the `/v1` `think` field is ignored) — `OllamaProvider` routes the reasoning model on schema roles there; proven by `tests/llm/test_no_think_live.py` (flat + nested schema). Verified details in `plan/inference-substrate.md`.
- ✅ **[Phase 3 test-infra] RESOLVED in 6b (TASK-6b.0):** `ctl.sh test` now claims an exclusive slot (1..15) via `flock` → isolated DB `johnny5_test_be_<slot>` + distinct Redis db + `--rm` container, with a SIGTERM trap (kills the container in ~1s) + a lock-free-orphan reaper. Parallel runs are safe by construction; the 6a collision class is eliminated. Verified: two overlapping `tests/sleep` runs → slots 1 & 2, both 21 passed concurrently, zero orphans.
