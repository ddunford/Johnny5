# Test Plan: Phase 1 — Memory spine

## Prerequisites
- Phase 0 complete; stack up; migrations applied. Tests run against `johnny5_test` DB, never dev.

## Test Cases

### TC-1.1: Episodic write + read-back
**Steps:** Write an episode; fetch by id.
**Expected:** Persisted with ts, content, salience, and a 1024-d embedding. Read-back matches.
**Status:** ⬜

### TC-1.2: Hybrid recall ranking
**Steps:** Seed episodes: (a) topically similar but old + low salience, (b) topically similar, recent, high salience, (c) unrelated. Recall with a query matching the topic, k=2.
**Expected:** (b) ranks above (a); (c) excluded. Pure-similarity ordering would tie (a) and (b) — the recency+salience weighting must separate them.
**Status:** ⬜

### TC-1.3: Semantic facts + graph edges
**Steps:** Upsert two facts; link them with a relation; recall by query; traverse the edge.
**Expected:** Facts recalled by similarity; edge returns the linked fact. Re-upserting the same subject/predicate updates, not duplicates.
**Status:** ⬜

### TC-1.4: Procedural skill reinforcement
**Steps:** Store a skill; `find` it by query; `reinforce` with success then failure.
**Expected:** Found by similarity; `success_rate` and `uses` update correctly.
**Status:** ⬜

### TC-1.5: Working memory bound + decay
**Steps:** Put items beyond capacity; advance time; call decay.
**Expected:** Capacity never exceeded; lowest-salience item evicted first; decayed items expire.
**Status:** ⬜

### TC-1.6: Consolidation stub runs
**Steps:** Write several related episodes; run `Consolidator.run()`.
**Expected:** At least one semantic fact created referencing source episode ids. (Quality is Phase 4 — here it must merely run and produce linked output.)
**Status:** ⬜

### TC-1.7: Persistence across restart
**Steps:** Write episodes + facts + a skill; snapshot; `./ctl.sh down && up`; recall.
**Expected:** All memory survives restart; snapshot restores into a clean DB identically.
**Status:** ⬜
