# Test Plan: Phase 4 — Self-Model + Metacognition + Sleep

## Prerequisites
- Phases 0–3 complete; stack up; `inference.lan` reachable (Groq + local qwen for the `@live` legs).
- Run as a **single `./ctl.sh test` runner** (concurrent runs corrupt the shared `johnny5_test` DB — `lessons.md`). DB-backed; deterministic tests use the frozen clock + stub router; `@pytest.mark.live` legs hit real models.
- Reuse the harness: `FrozenClock`, the loop-local engine + `simulate_restart` (`helpers/db`), the cycle harness (`helpers/cycle`). A clean-schema fixture covering the Phase-4 tables (`identity`, `self_improvement_note`, `sleep_log`) plus the memory/heartbeat tables a sleep run touches — mirror the `drives_db` pattern, scoped to only what a given test writes.

## Test Cases

### TC-4.1: Consolidation grows semantic memory from episodes
**Steps:** Seed a spread of recent episodes; run `Consolidator.run()` with a stub `consolidation` router returning a canned summary.
**Expected:** Episodes are embedding-clustered (not merely grouped by `kind`); one semantic fact per cluster is written carrying the **source episode ids** as provenance; the fact text is the summariser's output (not a naive concatenation). The P1 stub `_summarise` is gone (no caller references it).
**Status:** ⬜

### TC-4.2: Decay lowers salience without deleting; semantic facts merge
**Steps:** Seed old low-recall episodes + a goal/emotion-relevant one + two near-duplicate semantic facts; run `MemoryDecay.run()`.
**Expected:** Aged episodes' `salience` drops, the relevant episode is strengthened (or spared), **no episode row is deleted** (count unchanged — append-only invariant, `SPEC §8`); the two near-duplicate facts merge into one (cosine over the 1024-d embeddings), provenance unioned.
**Status:** ⬜

### TC-4.3: Sleep triggers on Energy depletion and restores it
**Steps:** With the frozen clock, drive the Energy drive over threshold so it emits `is_sleep_signal`; advance the run loop's sleep check.
**Expected:** Johnny enters the offline sleep phase (normal ticking paused); the pipeline runs; Energy is restored below threshold on wake; a `sleep_log` row records the run; the heartbeat resumes. Asserted deterministically (frozen clock, stub router).
**Status:** ⬜

### TC-4.4: Self-model refreshes and versions
**Steps:** Seed recent episodes + drive/mood history; run `SelfModel.refresh()` with a stub `self_model` router; read the current identity.
**Expected:** A new `identity` row at `version = previous + 1`, latest = current; self_model_doc/values/concerns/relationships reflect the projection; the refresh **read** the Core anchor (name/prime directive) but did not write it. `current()` returns the new version; a fresh instance after `simulate_restart` returns the same latest version (continuity).
**Status:** ⬜

### TC-4.5: Metacognition reviews and proposes (without applying)
**Steps:** Seed a mix of resolved + abandoned goals and some degraded ticks; run `Metacognition.review()` with a stub `metacognition` router.
**Expected:** A first-person review is produced; zero-or-more `self_improvement_note` rows are written with `status = open`; **nothing is applied** — no prompt/drive/agent/config is mutated, no self-edit is enacted (Phase 9 owns that). The review references the actual outcomes seeded (it's grounded, not generic).
**Status:** ⬜

### TC-4.6: The full sleep pipeline runs end-to-end
**Steps:** Trigger a sleep (TC-4.3) with episodes/goals seeded; let the whole pipeline run with stub routers.
**Expected:** In order — consolidation writes facts, decay/merge runs, self-model version bumps, metacognition writes a note, a snapshot is written, Energy restored, self-check passes, wake. The `sleep_log` row has populated counts (`facts_written`, `episodes_decayed`, `facts_merged`, `self_model_version`, `snapshot_path`, `self_check_ok=true`). A stage raising mid-sleep degrades **that stage only** and the loop still wakes (never wedged asleep).
**Status:** ⬜

### TC-4.7: Backups snapshot identity + drive/mood/goal (v2) and round-trip
**Steps:** Seed memory + identity + drives/mood/goals; `MemorySnapshot.snapshot()`; truncate; `restore()`.
**Expected:** The snapshot is `SNAPSHOT_VERSION = 2` and includes `identity`, `drive_state`, `mood`, `goal` alongside the P1 stores; restore into a clean DB reproduces all of it (ids preserved, sequences re-synced); a **v1 snapshot still restores** (back-compat). The snapshot dir is under the gitignored `snapshots/` tree.
**Status:** ⬜

### TC-4.8: A successful backup satisfies the Continuity drive
**Steps:** Raise the Continuity drive; run a sleep that completes a backup.
**Expected:** A `persistence_confirmed` drive event is emitted on backup success; the next appraise lowers the Continuity drive. (The loop between the safety backup and the felt "I won't be lost.")
**Status:** ⬜

### TC-4.9: Contract tests — consolidation / self-model / metacognition projections
**Steps:** Feed captured model envelopes (positive + degenerate/empty) to each pure projection (`consolidation` summary→fact, `self_model`→IdentityDoc, `metacognition`→Review).
**Expected:** Each projects correctly from a literal captured envelope; empty/garbage content **fails loudly** (no silent no-op), no reasoning-preamble leakage into stored content. Plus a `@pytest.mark.live` leg per role proving the real Groq/qwen path returns parseable structured output at the configured `max_tokens` (the token-budget lesson — deterministic tests can't catch it).
**Status:** ⬜

### TC-4.10: Wake self-check gates resume
**Steps:** (a) Run a sleep with an intact self-model + anchor. (b) Tamper: blank/corrupt the latest self-model doc, then attempt wake. (c) Simulate an altered Core anchor name.
**Expected:** (a) self-check passes, full agency resumes, `self_check_ok=true`. (b)+(c) self-check **fails**, full agency does **not** resume (degraded mode + alert logged), `self_check_ok=false` — Johnny does not wake into a corrupted identity. The check never mutates the Core anchor; it only compares.
**Status:** ⬜

### TC-4.11: Sleep/wake + self-model surface on `/ws/state` and the REPL
**Steps:** Connect to `/ws/state` (with `WS_TOKEN`); trigger a sleep; run the REPL `/state` (or `/self`) view.
**Expected:** `/ws/state` reflects awake→asleep→awake transitions, the last consolidation summary, and the current self-model version (stable schema, same `WS_TOKEN` gate). The REPL shows sleep status, the identity doc, and the latest metacognitive reflection. Surfaces emit whether or not a client is attached (FC-8).
**Status:** ⬜

### TC-4.12: No regression — the awake heartbeat is unchanged
**Steps:** Run the cognition + drives suites after the Phase-4 wiring.
**Expected:** Phases 2–3 behaviour intact — tick pipeline, attention bottleneck, drive/affect/goal/deliberation, bounded cycle rate — all still green; sleep only engages on the Energy trigger and otherwise the loop ticks exactly as before. Full suite green 3× (determinism, single runner).
**Status:** ⬜
