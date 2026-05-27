# Test Plan: Phase 1 — Memory spine

## Prerequisites
- Phase 0 complete; stack up; migrations applied. Tests run against `johnny5_test` DB, never dev.

## Test Cases

### TC-1.1: Episodic write + read-back
**Steps:** Write an episode; fetch by id.
**Expected:** Persisted with ts, content, salience, and a 1024-d embedding. Read-back matches.
**Status:** ✅ Pass — `tests/memory/test_episodic_recall.py::test_write_persists_and_reads_back` (+ salience clamp).

### TC-1.2: Hybrid recall ranking
**Steps:** Seed episodes: (a) topically similar but old + low salience, (b) topically similar, recent, high salience, (c) unrelated. Recall with a query matching the topic, k=2.
**Expected:** (b) ranks above (a); (c) excluded. Pure-similarity ordering would tie (a) and (b) — the recency+salience weighting must separate them.
**Status:** ✅ Pass — `tests/memory/test_episodic_recall.py`: ranking test asserts [b, a] order + (c) excluded; a companion test proves similarity-only weighting ties (a)/(b), so the blend is what separates them. Deterministic axis-vector embeddings + injected `now`.

### TC-1.3: Semantic facts + graph edges
**Steps:** Upsert two facts; link them with a relation; recall by query; traverse the edge.
**Expected:** Facts recalled by similarity; edge returns the linked fact. Re-upserting the same subject/predicate updates, not duplicates.
**Status:** ✅ Pass — `tests/memory/test_semantic_memory.py`: similarity recall, edge traversal + relation filter, idempotent link, and re-upsert-updates-in-place (single row).

### TC-1.4: Procedural skill reinforcement
**Steps:** Store a skill; `find` it by query; `reinforce` with success then failure.
**Expected:** Found by similarity; `success_rate` and `uses` update correctly.
**Status:** ✅ Pass — `tests/memory/test_procedural_memory.py`: find-by-intent, reinforce success→failure (uses/successes/rate exact), re-store preserves history, unknown-skill raises.

### TC-1.5: Working memory bound + decay
**Steps:** Put items beyond capacity; advance time; call decay.
**Expected:** Capacity never exceeded; lowest-salience item evicted first; decayed items expire.
**Status:** ✅ Pass — `tests/memory/test_working_memory.py`: capacity bound + least-salient eviction, decay scales-then-evicts-below-floor, TTL expiry against injected FrozenClock, zero-TTL = no expiry. Loop-local Redis client on the test DB.

### TC-1.6: Consolidation stub runs
**Steps:** Write several related episodes; run `Consolidator.run()`.
**Expected:** At least one semantic fact created referencing source episode ids. (Quality is Phase 4 — here it must merely run and produce linked output.)
**Status:** ✅ Pass — `tests/memory/test_consolidator.py`: `run()` distils same-kind episodes into a fact carrying the source episode ids, recallable via `semantic.recall` (provenance preserved); one fact per kind; empty episodes → empty result.

### TC-1.7: Persistence across restart
**Steps:** Write episodes + facts + a skill; snapshot; `./ctl.sh down && up`; recall.
**Expected:** All memory survives restart; snapshot restores into a clean DB identically.
**Status:** ✅ Pass — `tests/memory/test_restart_persistence.py`: (1) episodes survive a simulated restart (global engine + all connections disposed and rebuilt → still recalled by id/content); (2) snapshot → wipe to clean DB + empty working set → restore reproduces all four Postgres stores + working memory **identically** (ids, content, embeddings, edges, skills, working items compared row-for-row); (3) restore is idempotent (no row duplication). In-process restart stands in for `./ctl.sh down && up`.
