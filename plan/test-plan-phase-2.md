# Test Plan: Phase 2 — Heartbeat + Global Workspace

## Prerequisites
- Phases 0–1 complete; stack up; `inference.lan` reachable.

## Test Cases

### TC-2.1: The heartbeat runs continuously
**Steps:** `./ctl.sh up`; observe for ~60s with no input via the REPL.
**Expected:** The cycle ticks at a steady interval; thoughts are produced each (notable) tick; the loop neither stalls nor busy-spins. Frozen-clock test runs N deterministic ticks reproducibly.
**Status:** ⬜

### TC-2.2: Input becomes a percept and shifts attention
**Steps:** In the REPL, inject "I just adopted a dog named Pixel."
**Expected:** A `percept` row is created; on the next tick Attention promotes it into the workspace; the narration references it. Dump shows it in the workspace contents.
**Status:** ⬜

### TC-2.3: Attention is a bottleneck
**Steps:** Inject many low-salience percepts plus one clearly salient one.
**Expected:** The workspace holds only a bounded set; the salient item is selected; trivial ones are excluded. Workspace size never grows unbounded.
**Status:** ⬜

### TC-2.4: Every broadcast is logged
**Steps:** Run for several ticks; query `workspace_event`.
**Expected:** One row per broadcast with module, type, payload, ts — sufficient to replay the tick.
**Status:** ⬜

### TC-2.5: Narration is memory-grounded
**Steps:** Pre-seed an episode ("Dan prefers concise answers"); run idle.
**Expected:** Within a few ticks the recall step surfaces it and the narration reflects it — demonstrating memory feeds cognition, not just storage.
**Status:** ⬜

### TC-2.6: Resilience — loop survives provider failure
**Steps:** Force the narrator's provider to fail (point Qwen at a dead host) for ~30s, then restore.
**Expected:** The heartbeat continues (degraded narration or skipped narrate step); no crash; recovers automatically when the provider returns.
**Status:** ⬜

### TC-2.7: Live consciousness stream
**Steps:** Connect to `/ws/consciousness`.
**Expected:** Thoughts stream in real time as they're produced; reconnect resumes cleanly.
**Status:** ⬜
