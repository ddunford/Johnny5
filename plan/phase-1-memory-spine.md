# Phase 1: Memory spine

## Overview
Johnny's memory, built before any cognition so the cognitive cycle (Phase 2) has somewhere to write and recall from. Four stores — working, episodic, semantic, procedural — with embedding-based hybrid recall and the episodic write path. Consolidation is stubbed here (the interface + a naive pass) and made real in Phase 4. No drives, no narrator yet: this phase delivers a memory you can write to and query.

**Done when:** you can write an episode, recall relevant episodes by a query (blending similarity + recency + salience), promote facts into semantic memory, store/retrieve a skill, and everything survives a restart. All memory persists to disk and is snapshotable.

## Custom Feature: Four-tier memory

**Purpose:** Johnny's continuity and growth depend on memory. No module covers the four-tier model + the recency×relevance×importance recall blend (Generative Agents pattern) + consolidation. Built on Postgres+pgvector and the Phase 0 Embedder.

**Database tables:** (from `SPEC.md §12`)
| Table | Key columns | Notes |
|-------|------------|-------|
| `episode` | id, ts, kind, content, actors, emotion_tags, salience, embedding vector(1024) | append-only autobiography; never hard-deleted in v1 |
| `semantic_fact` | id, subject, predicate, object, confidence, source_episode_ids[], embedding vector(1024) | consolidated knowledge |
| `semantic_edge` | id, from_fact, to_fact, relation | light knowledge graph |
| `skill` | id, name, recipe jsonb, success_rate, uses, embedding vector(1024) | procedural memory |

Working memory is **Redis** (bounded, TTL/decay), not a table.

**Internal interfaces:**
- `EpisodicMemory.write(episode)` / `.recall(query, k) -> [Episode]` — recall scores `w1·similarity + w2·recency + w3·salience`, weights configurable.
- `SemanticMemory.upsert_fact(...)` / `.recall(query, k)` / `.link(a, b, rel)`.
- `ProceduralMemory.store(skill)` / `.find(query) -> [Skill]` / `.reinforce(skill_id, success)`.
- `WorkingMemory.put(item, ttl)` / `.contents() -> [Item]` / `.decay()` — bounded capacity; least-salient evicted when full.
- `Consolidator.run()` — STUB this phase (cluster recent episodes, naive summary → semantic). Real impl Phase 4.

**Service modules:** `brain/memory/{episodic,semantic,procedural,working,consolidator}.py`, repositories behind interfaces, ivfflat/hnsw index on the vector columns.

**Key patterns (non-obvious):**
- Recall is **hybrid**, not pure vector — pure cosine similarity recalls topically-similar but stale/trivial memories; recency + salience are what make recall feel mind-like (Generative Agents finding). Don't ship vector-only recall.
- Embeddings via the Phase 0 Embedder (TEI BGE-M3, 1024-d) — never embed inline; always through the router/embedder so it's logged and circuit-broken.
- Working memory has bounded capacity *on purpose* (it feeds the LLM context budget) — it is the precursor to the Attention bottleneck in Phase 2.

**Test checklist:** see `test-plan-phase-1.md`.

## Implementation steps
1. Migrations for `episode`, `semantic_fact`, `semantic_edge`, `skill` + vector indexes.
2. Episodic memory: write + hybrid recall (similarity/recency/salience blend).
3. Semantic memory: fact upsert, recall, edge linking.
4. Procedural memory: skill store/find/reinforce.
5. Working memory on Redis: bounded buffer with decay + salience-based eviction.
6. Consolidator stub: interface + naive episodic→semantic pass (callable, not scheduled).
7. Snapshot/restore: dump + reload memory (continuity / backup foundation).
8. Tests: repository unit tests, recall-ranking tests (deterministic embeddings), restart-persistence test.

## Tasks

- [x] `TASK-1.1` Migrations: `episode`, `semantic_fact`, `semantic_edge`, `skill` + vector indexes → `/fastapi-engineer`
- [x] `TASK-1.2` Episodic memory: write path + hybrid recall (similarity×recency×salience) → `/fastapi-engineer` [TC-1.1, TC-1.2]
- [x] `TASK-1.3` ⫘ Semantic memory: fact upsert, recall, edge linking → `/fastapi-engineer` [TC-1.3]
- [x] `TASK-1.4` ⫘ Procedural memory: skill store/find/reinforce → `/fastapi-engineer` [TC-1.4]
- [x] `TASK-1.5` Working memory (Redis): bounded buffer, decay, salience eviction → `/fastapi-engineer` [TC-1.5]
- [x] `TASK-1.6` Consolidator stub: interface + naive episodic→semantic pass → `/fastapi-engineer` [TC-1.6]
- [x] `TASK-1.7` Snapshot/restore of all stores (continuity foundation) → `/fastapi-engineer` [TC-1.7]
- [ ] `TASK-1.8` Memory repository unit tests + recall-ranking tests (deterministic/seeded embeddings) → `/qa-test-engineer` [TC-1.1, TC-1.2, TC-1.3, TC-1.4]
- [ ] `TASK-1.9` Restart-persistence integration test (write → restart → recall) → `/qa-test-engineer` [TC-1.7]
- [x] `TASK-1.10` ⫘ Security review: memory snapshots gitignored, no PII in logs, parameterised vector queries → `/security-reviewer`
  - Verdict: **PASS — no Critical/High.** Vector/edge/recall queries parameterised (SQLAlchemy constructs); the only f-string SQL is identifier interpolation from a hardcoded `_TABLES` allowlist in snapshot.py (table names can't be bind-params — allowlist is correct/safe). Snapshots write only to gitignored `memory_snapshot_dir` (none tracked); no PII/secret logging; embedding content goes to the Embedder, never into SQL. (Local-only snapshots are trusted; revisit if snapshots ever become shareable/remote.)

## Notes
- No UI and no live-running loop yet; verification is pytest + a REPL/script that writes and recalls. No Playwright.
- Recall weight defaults (`w1/w2/w3`) are config, not hardcoded — Phase 3 Affect and Phase 4 reflection will tune them.
