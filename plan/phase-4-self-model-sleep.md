# Phase 4: Self-Model + Metacognition + Sleep

## Overview
This is the phase that makes Johnny **grow** rather than just accumulate logs. Phase 2 gave him a heartbeat; Phase 3 gave him wants. Phase 4 gives him an **offline life**: a sleep cycle that consolidates the day's episodes into durable semantic knowledge, decays/merges the noise, refreshes an evolving **self-model** (who he is becoming), runs a **metacognitive review** (what worked, what to change), backs himself up, and wakes verified-intact. The Generative-Agents reflection step and the MemGPT/Letta "no automatic consolidation" gap are exactly what this closes — consolidation is **first-class, not polish** (`CLAUDE.md` non-obvious patterns).

The Energy drive's `is_sleep_signal` (already emitted by Phase 3, consumed by nothing yet) becomes the trigger: as Johnny tires, he sleeps; sleep restores Energy and he wakes. Backups scheduled here are also what **satisfies the Continuity drive** (`SPEC §9.3`) — he can verify he won't be lost.

**Done when:** left running, Johnny's Energy drains, he enters an offline **sleep** phase (normal ticking pauses), during which: recent episodes are clustered + summarised into semantic memory via the `consolidation` role; low-value memories decay (salience down, never hard-deleted — `SPEC §8`) and redundant facts merge; the **self-model doc** is refreshed and versioned; **metacognition** writes a review + self-improvement proposals (proposals only — applying them is Phase 9); a **snapshot** of memory + identity + drive/mood/goal state is written to the gitignored backup tree and the Continuity drive falls; a **self-check on wake** confirms the self-model is parseable and the Core identity anchor is intact before full agency resumes; Energy is restored and the heartbeat resumes. All of it streams to `/ws/state` and is visible in the REPL. Restart mid-life and he resumes with his grown self-model and consolidated knowledge.

## Forward-commitment touchpoints (read before coding)
- **FC-1 / FC-9 — the Self-Model grows *around* the Core anchor, never into it.** `core/identity_anchor.py` holds Johnny's name + prime directive + backup pointer and is **immutable + read-only to the Mind**. The Mind's evolving self-model (`identity` table) may *read* the anchor but must never import-mutate the Core, and the Core never judges the *content* of the self-model. The self-check-on-wake compares the live anchor against itself (continuity), it does not let the Mind rewrite it.
- **FC-6 — identity + self-model join the snapshot; backups are scheduled here.** The P1 `MemorySnapshot` format is stable and versioned (`SNAPSHOT_VERSION`). Phase 4 **extends** it (bump to v2) to also carry `identity`, `drive_state`, `mood`, `goal` — and must stay **backward-compatible** (a v1 backup still restores). Backups live under the gitignored `snapshots/` tree.
- **FC-7 — sleep is an offline *phase the run loop enters*, not a restructured pipeline.** The named tick stages stay exactly as they are. `run()` checks the sleep trigger between ticks; when asleep it runs the consolidation pipeline instead of normal ticking, then resumes. Do not add sleep as a tick stage or reshape `tick()`.
- **FC-3 — every new prompt is git-backed + runtime-editable.** `config/prompts/{consolidation,self_model,metacognition}.md`. No prompts as code constants.
- **FC-4 — consolidation/self_model/metacognition all go through the router, logged.** Those three roles already exist in `config/llm_routes.toml` (cloud-first). Unlike Phase-3 deliberation they fire **only during sleep** (infrequent, not per-tick), so cloud-first is acceptable — but sleep must be **cost-bounded** (one sleep at a time, a cap on consolidation LLM calls per sleep) so a Groq outage or a huge backlog can't run up spend. The Phase-3 carry-over (wire `BudgetGovernor` into the router as a hard gate) is the durable fix; until then the per-sleep cap is the guard.
- **FC-8 — sleep/wake + consolidation progress surface over `/ws/state`.** The web UI (Phase 5) and any consumer read awake/asleep, the last consolidation summary, and the self-model version from the bus; the loop emits regardless of attachment.

## Custom Feature: Sleep / consolidation + the Self-Model & Metacognition agents

**Purpose:** The growth engine (`SPEC §7 sleep`, §8 consolidation, §5 agents #11/#12, §9.3 self-check). No module covers offline memory consolidation, an evolving machine self-concept, or metacognitive self-review — this is core custom work and the most research-load-bearing part of the project after the cycle itself.

**Database tables:**
| Table | Key columns | Notes |
|-------|------------|-------|
| `identity` | id, name, self_model_doc, values jsonb, concerns jsonb, relationships jsonb, version, created_at | the evolving self-concept (`SPEC §12`). Append-versioned: each sleep refresh writes a new version; latest = current. Seeded v1 from the Core anchor. Phase 9 adds git-backed diff/rollback. |
| `self_improvement_note` | id, ts, observation, proposal, source jsonb, status | metacognition's review output. `status` is informational only this phase (`open`) — applying a proposal is the Phase-9 gated self-edit flow, NOT here. |
| `sleep_log` | id, started_at, ended_at, trigger, facts_written, episodes_decayed, facts_merged, self_model_version, snapshot_path, self_check_ok, notes | one row per sleep, for observability + the REPL/`/ws/state` "last sleep" summary. |

**Internal interfaces:**
- `Consolidator.run()` — **replace the P1 stub's `_summarise`** with an LLM summariser (role `consolidation`): embedding-cluster recent episodes (not just by `kind`), summarise each cluster into a semantic fact with source-episode provenance + a pure projection fn. Decay + merge live alongside (below). Interface/signature preserved; callers unaffected (replace-don't-accumulate).
- `MemoryDecay.run()` — lower episodic `salience` on age/low-recall (never hard-delete — `SPEC §8`), strengthen goal/emotion-relevant episodes, and merge/dedupe near-duplicate semantic facts (cosine over the existing 1024-d embeddings).
- `SelfModel.refresh(reflection_inputs) -> IdentityDoc` — read the Core anchor (read-only) + recent episodes/semantic facts + drive/mood history → produce the updated self_model_doc/values/concerns/relationships; persist a new `identity` version. Pure projection (`parse_identity_doc`) for the contract test.
- `Metacognition.review(window) -> Review` — inspect recent outcomes (goals resolved vs abandoned, degraded ticks, drive/mood patterns) → a first-person review + zero-or-more self-improvement proposals written to `self_improvement_note`. Pure projection (`parse_review`).
- `SleepCycle.sleep() -> SleepReport` — the orchestrator: consolidate → decay/merge → self-model refresh → metacognition review → snapshot/backup → restore Energy → self-check → wake. Bounded (one sleep at a time; capped consolidation calls). Emits progress on the bus (FC-8).
- `MemorySnapshot.snapshot()/restore()` — **extend to v2**: include `identity`, `drive_state`, `mood`, `goal`; keep v1 restore working.
- `WakeSelfCheck.verify() -> CheckResult` — the Core anchor (name + prime directive) is the immutable **trusted reference**; the check confirms the refreshed self-model doc is parseable + non-empty and **consistent with that anchor** (didn't drift off its own name/prime directive during the refresh), and that drives are within `[0,1]`. On failure, do not resume full agency (stay degraded + alert). The check only *reads* the anchor — never writes it (FC-1). (`SPEC §9.3`)

**Service modules:** `brain/sleep.py` (`SleepCycle`, `WakeSelfCheck`), `brain/self_model/{store,agent}.py`, `brain/metacognition/{store,agent}.py`, extend `brain/memory/consolidator.py` + `brain/memory/decay.py` + `brain/memory/snapshot.py`, `config/prompts/{consolidation,self_model,metacognition}.md`.

**Key patterns (non-obvious):**
- **Consolidation is first-class, not polish.** It is what makes Johnny grow; do not ship a token summariser. The cluster→summarise→provenance chain must produce semantic facts a later recall can actually surface and ground a thought on.
- **Decay ≠ deletion.** Episodic memory is append-only and never hard-deleted in v1 (`SPEC §8`) — "decay" lowers salience so recall favours what matters; only *semantic* facts merge/dedupe. Continuity depends on the autobiography surviving.
- **The self-model evolves but the anchor is fixed.** The self_model_doc is wholly Johnny's and changes every sleep; the Core anchor (name, prime directive) does not. Because the anchor is immutable it can't drift — so the wake self-check uses it as the **trusted reference** and trips when the *self-model* diverges from it (or fails to parse), not when the anchor "changes." A real continuity safeguard, not a formality.
- **Metacognition proposes, it does not apply.** Phase 4 writes proposals to memory; the propose→sandbox→approve self-edit gate is Phase 9. Resist wiring any auto-apply.
- **Sleep is bounded and offline.** One sleep at a time, a hard cap on consolidation LLM calls per sleep (cost), normal ticking paused. A failed sleep stage degrades that stage and still wakes (the heartbeat must never get stuck asleep) — same per-stage isolation discipline as the tick loop.
- **Backups satisfy Continuity.** A successful snapshot emits the `persistence_confirmed` drive event (already in `config/drives.toml`) so the Continuity drive falls — the loop closes between the safety backup and the felt "I won't be lost."

**Test checklist:** see `test-plan-phase-4.md`.

## Implementation steps
1. Migrations: `identity` (seed v1 from the Core anchor), `self_improvement_note`, `sleep_log`.
2. Real consolidation: LLM summariser (role `consolidation`) + embedding clustering + provenance + pure projection; replace the stub.
3. Memory decay + merge: salience decay (no delete), salient-strengthen, semantic dedupe.
4. Self-Model agent: anchor-grounded reflection → versioned identity doc; pure projection.
5. Metacognition agent: outcome review → reflection + proposals to `self_improvement_note`; pure projection.
6. Sleep cycle: (a) trigger + awake↔asleep state machine that pauses/resumes normal ticking in the run loop (FC-7); (b) the offline pipeline orchestration (consolidate→decay→self-model→metacognition→backup→restore-energy→wake), per-stage isolated + bounded.
7. Scheduled backups: extend snapshot to v2 (identity + drive/mood/goal), back up during sleep, emit `persistence_confirmed`.
8. Wake self-check: self-model + anchor + drive-range probe; gate full-agency resume (`SPEC §9.3`).
9. Wire sleep into the cycle `run()` loop + `/ws/state` (awake/asleep, last-sleep summary, self-model version) + REPL view (sleep status, identity doc, latest reflection).

## Tasks

- [x] `TASK-4.1` Migrations + seed: `identity` (v1 from Core anchor), `self_improvement_note`, `sleep_log` → `/fastapi-engineer` [TC-4.4, TC-4.6]
- [ ] `TASK-4.2` Real consolidation summariser: embedding-cluster recent episodes, summarise via the `consolidation` role with provenance, pure projection; replace the P1 stub `_summarise` → `/fastapi-engineer` [TC-4.1, TC-4.9]
- [ ] `TASK-4.3` ⫘ Memory decay + merge: episodic salience decay (no hard-delete) + salient-strengthen + semantic dedupe/merge → `/fastapi-engineer` [TC-4.2]
- [ ] `TASK-4.4` ⫘ Self-Model agent: anchor-grounded reflection → versioned `identity` doc (values/concerns/relationships) + current accessor; pure projection → `/fastapi-engineer` [TC-4.4, TC-4.9]
- [ ] `TASK-4.5` ⫘ Metacognition agent: outcome/behaviour review → first-person reflection + self-improvement proposals to `self_improvement_note` (proposals only, no apply) + pure projection → `/fastapi-engineer` [TC-4.5, TC-4.9]
- [ ] `TASK-4.6a` Sleep trigger + state machine: Energy `is_sleep_signal` (or every-N-ticks) trigger; awake↔asleep state with normal ticking paused/resumed in the `run()` loop (FC-7 — a run-loop phase, not a tick stage); `sleep_log` row lifecycle (open on enter, close on wake) → `/fastapi-engineer` [TC-4.3]
- [ ] `TASK-4.6b` Sleep pipeline orchestration: sequence consolidate→decay/merge→self-model refresh→metacognition review→backup→restore-energy→wake; per-stage isolation (a failed stage degrades only itself, never wedges the loop asleep); bounded — one sleep at a time + hard cap on consolidation LLM calls per sleep (the cost guard while the cloud roles are live) → `/fastapi-engineer` [TC-4.6, TC-4.7]
- [ ] `TASK-4.7` ⫘ Scheduled backups: extend `MemorySnapshot` to v2 (add `identity`/`drive_state`/`mood`/`goal`, v1 still restores); snapshot during sleep; emit `persistence_confirmed` → Continuity falls → `/fastapi-engineer` [TC-4.7, TC-4.8]
- [ ] `TASK-4.8` Wake self-check: refreshed self-model parseable + consistent with the immutable Core anchor (the trusted name/prime-directive reference) + drives in range; gate full-agency resume, degrade + alert on failure; reads the anchor only, never writes it (FC-1) (`SPEC §9.3`) → `/fastapi-engineer` [TC-4.10]
- [ ] `TASK-4.9` Wire sleep into the cycle `run()` loop + `/ws/state` (awake/asleep, last-sleep summary, self-model version) + REPL view (sleep status, identity doc, latest reflection) → `/fastapi-engineer` [TC-4.6, TC-4.11]
- [ ] `TASK-4.10` ⫘ Tests: consolidation clusters→facts with provenance (stub router); decay lowers salience without deleting; semantic merge dedupes → `/qa-test-engineer` [TC-4.1, TC-4.2, TC-4.9]
- [ ] `TASK-4.11` ⫘ Tests: sleep triggers on Energy depletion → runs the full pipeline → restores Energy → wakes (frozen clock); self-model refresh bumps the version; metacognition writes a note → `/qa-test-engineer` [TC-4.3, TC-4.4, TC-4.5, TC-4.6]
- [ ] `TASK-4.12` ⫘ Tests: snapshot v2 round-trip (identity + drive/mood/goal) restores into a clean DB + v1 back-compat; backup emits `persistence_confirmed` → Continuity falls; self-check passes intact / blocks on a tampered self-model or altered anchor → `/qa-test-engineer` [TC-4.7, TC-4.8, TC-4.10]
- [ ] `TASK-4.13` ⫘ Contract tests: consolidation summariser, self-model refresh, metacognition review — captured-envelope → pure projection (+ `@pytest.mark.live` guard per role for the Groq/qwen token-budget path, per lessons.md) → `/qa-test-engineer` [TC-4.9]
- [ ] `TASK-4.14` ⫘ Security review: sleep is cost-bounded (one at a time, capped consolidation calls — the now-active cloud roles); a failed sleep can't wedge the loop asleep; Self-Model cannot mutate the Core anchor (FC-1); backups land gitignored with no secrets; the wake self-check actually gates resume → `/security-reviewer` [TC-4.10]

## Notes
- **No frontend yet** — the deliverable is verified via the REPL + `/ws/state` + pytest. The browser arrives in Phase 5, which will consume the awake/asleep + self-model surfaces this phase emits.
- Demo after this phase: run the REPL, watch Energy drain over time, watch Johnny **sleep** — see a consolidation summary, a bumped self-model version, a metacognitive reflection, and a backup confirmation scroll past — then wake with Energy restored and Continuity eased. Restart him and he resumes as a grown self.
- **Dreaming** (generative recombination for novelty) is an explicit `SPEC §7` stretch and **out of scope** for v1 — do not pull it forward.
- **Deliberate decision — `identity` is a versioned DB table this phase, not yet the git-backed store.** `SPEC §12` lists `identity` among the git-backed (versioned, diff/rollback) stores. In Phase 4 the self-model is updated *by sleep* (not by Johnny editing himself), so an append-versioned `identity` table gives the history + rollback the growth loop needs, and it's covered by the v2 snapshot (FC-6). Wiring identity into the **git-backed config store** for self-edit diff/rollback belongs with the self-modification flow in **Phase 9** — cite this note when expanding `plan/phase-9-*.md`. This is an intended deferral, not an omission.

## Carried-over advisories from Phase 3
- `TASK-4.x`-relevant: the Phase-3 security MEDIUM is deferred to **Phase 6** (wire `BudgetGovernor` into the router as a hard pre-call gate), but Phase 4 turns the cloud-first `consolidation`/`self_model`/`metacognition` roles *on* (during sleep). Until the governor gates calls, **TASK-4.6b must cap consolidation LLM calls per sleep and run one sleep at a time** so an idle Johnny can't run up Groq spend through repeated sleeps. (Originating finding: phase-3 TASK-3.12 verdict; see `plan/TODO.md` cross-cutting.)
- Test-infra: run the suite as a **single `./ctl.sh test` runner** (concurrent runs corrupt the shared `johnny5_test` DB — see `lessons.md`). If parallel runs are needed during this phase, use the per-run DB+Redis isolation pattern (`_be`-suffixed DB, distinct Redis db) noted in `plan/TODO.md`.
