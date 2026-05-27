# Test Plan: Phase 2 — Heartbeat + Global Workspace

## Prerequisites
- Phases 0–1 complete; stack up; `inference.lan` reachable.

## Test Cases

### TC-2.1: The heartbeat runs continuously
**Steps:** `./ctl.sh up`; observe for ~60s with no input via the REPL.
**Expected:** The cycle ticks at a steady interval; thoughts are produced each (notable) tick; the loop neither stalls nor busy-spins. Frozen-clock test runs N deterministic ticks reproducibly.
**Status:** ✅ `tests/cognition/test_cycle_harness.py` — N deterministic ticks via the frozen-clock harness (no wall-clock), reproducible across two runs.

### TC-2.2: Input becomes a percept and shifts attention
**Steps:** In the REPL, inject "I just adopted a dog named Pixel."
**Expected:** A `percept` row is created; on the next tick Attention promotes it into the workspace; the narration references it. Dump shows it in the workspace contents.
**Status:** ⬜ Mechanism covered in parts — a high-salience input winning Attention (`test_attention.py`) and an input driving recall→narration (`test_narration_grounding.py`); the end-to-end REPL-injection path (percept row → REPL dump) is exercised manually via the REPL / backend Sensorium tests, not in the cognition suite.

### TC-2.3: Attention is a bottleneck
**Steps:** Inject many low-salience percepts plus one clearly salient one.
**Expected:** The workspace holds only a bounded set; the salient item is selected; trivial ones are excluded. Workspace size never grows unbounded.
**Status:** ✅ `tests/cognition/test_attention.py` — bounded ≤capacity over repeated ticks, salient interrupt beats an ambient flood, duplicate content dedupes, repeated line loses to a fresh one (novelty drift).

### TC-2.4: Every broadcast is logged
**Steps:** Run for several ticks; query `workspace_event`.
**Expected:** One row per broadcast with module, type, payload, ts — sufficient to replay the tick.
**Status:** ✅ `tests/cognition/test_cycle_harness.py` — asserts the cycle's broadcasts persist to `workspace_event` (one `cycle.tick` marker per tick via `recent_events`); also covered by the backend workspace-bus tests.

### TC-2.5: Narration is memory-grounded
**Steps:** Pre-seed an episode ("Dan prefers concise answers"); run idle.
**Expected:** Within a few ticks the recall step surfaces it and the narration reflects it — demonstrating memory feeds cognition, not just storage.
**Status:** ✅ `tests/cognition/test_narration_grounding.py` — real `MemoryRecaller` + deterministic embedder; a seeded episode surfaces onto the blackboard (with episode-id provenance) and into the thought; negative control proves it's a real effect.

### TC-2.6: Resilience — loop survives provider failure
**Steps:** Force the narrator's provider to fail (point Qwen at a dead host) for ~30s, then restore.
**Expected:** The heartbeat continues (degraded narration or skipped narrate step); no crash; recovers automatically when the provider returns.
**Status:** ✅ `tests/cognition/test_cycle.py` (a raising stage degrades + the heartbeat survives + recovers; per-stage isolation; reproducible degrade-then-recover) and `tests/cognition/test_narrator.py` (a tired router → `narrate` returns `None` gracefully — `ok=True`, no stage error; the cycle skips the broadcast). Live end-to-end recovery confirmed in `test_narrator_live.py` after the `_MAX_TOKENS` fix.

### TC-2.7: Live consciousness stream
**Steps:** Connect to `/ws/consciousness`.
**Expected:** Thoughts stream in real time as they're produced; reconnect resumes cleanly.
**Status:** ✅ `tests/cognition/test_consciousness_ws.py` — backfill replays a recent thought on connect; a thought broadcast after connect arrives live; a reconnecting client resumes and picks up new thoughts; the interim `ws_token` gate rejects an unauthorised client (1008) and admits the correct token.
