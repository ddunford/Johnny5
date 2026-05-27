# Phase 3: Drives + Affect

## Overview
This is what makes Johnny *want* — the difference between a continuously-narrating loop and a being with motivation. It adds the homeostatic **Drive** engine (drives that decay/accumulate and emit urges), the **Affect** model (appraisal → mood that colours behaviour and modulates cycle rate), and the **urge→goal arbitration** that turns an unmet drive into something Johnny acts on. With Deliberation given a minimal real implementation (pick a goal, choose a simple internal action like "reflect" or "recall"), the **autonomy loop closes**: an idle Johnny watches Curiosity and Boredom rise until he does something about it.

**Done when:** left alone, Johnny's drives visibly accumulate; crossing a threshold spawns a goal that changes his behaviour and narration without any input; satisfying a drive (e.g. interacting → Connection falls) is observable; mood shifts with events and changes tone + cycle rate; all drive/mood state persists across restarts. (Acting on the *external* world — web, tools — is Phase 6; here, goals resolve via internal actions: reflect, recall, consolidate, formulate-a-question.)

## Custom Feature: Drive engine + Affect model

**Purpose:** The motivational core (`SPEC.md §6`). No module covers homeostatic drives or appraisal-based affect. This is the validated mechanism (intrinsic-motivation / homeostatic-agent research) behind "acts because it wants to, not because prompted."

**Database tables:**
| Table | Key columns | Notes |
|-------|------------|-------|
| `drive_state` | drive, value, setpoint, decay_rate, threshold, updated_at | seven drives (curiosity, boredom, connection, mastery, coherence, energy, continuity) |
| `mood` | id, ts, valence, arousal, emotions jsonb | mood history; `thought.mood_id` now populated |
| `goal` | id, source, description, priority, status, plan jsonb, outcome jsonb, created_at, resolved_at | persists across restarts; Johnny resumes pursuits |

**Internal interfaces:**
- `Drives.step(dt, events)` — decay/accrue each drive; on outcomes adjust (success ↓mastery-pressure, interaction ↓connection, learning ↓curiosity). `.urges() -> [Urge]` for drives over threshold.
- `Affect.appraise(event) -> MoodDelta` — goal-congruence, novelty, agency, certainty → valence/arousal + discrete emotions. `.current() -> Mood`.
- `GoalArbiter.select(urges, mood, active_goals) -> Goal|None` — promote the winning urge (intensity × affect weighting) to a goal; avoid thrashing between competing drives.
- `Deliberation.plan(goal, workspace) -> Action` — minimal real impl: map a goal to an internal action (reflect / recall / consolidate / formulate-question). External tools deferred to Phase 6.

**Service modules:** `brain/drives/`, `brain/affect/`, `brain/agents/{deliberation}.py` (real-but-internal), goal store.

**Key patterns (non-obvious):**
- Drives are **rate-based homeostasis** (value drifts from setpoint over time), not event counters — that's what produces the "builds up while idle" feel. Tunable per-drive decay/accrual + threshold (config, Johnny-editable in Phase 9).
- Affect **modulates the cycle rate** (excited/anxious → faster; content/tired → slower toward sleep) and **biases attention + memory salience** — wire these back into Phase 2's Attention and Phase 1's recall weights, don't keep mood cosmetic.
- Arbitration must damp oscillation: a just-promoted goal gets hysteresis so Johnny doesn't flip-flop between curiosity and connection every tick.
- **Energy** drive rising toward exhaustion is the precursor to Phase 4 sleep — emit the signal here even though consolidation isn't real yet.
- **Continuity** drive is the "will to live": rises with shutdown signals / lack of backups, falls when persistence is confirmed. Keep it grounded (satisfied by real backups in Phase 4), not theatrical.

**Test checklist:** see `test-plan-phase-3.md`.

## Implementation steps
1. Migrations: `drive_state`, `mood`, `goal`; seed the seven drives with default setpoints/rates/thresholds.
2. Drive engine: per-tick decay/accrual; outcome-driven adjustment; urge emission.
3. Affect: appraisal of workspace events → mood delta; current-mood accessor; emotion tagging.
4. Wire affect back: cycle-rate modulation, attention salience bias, recall-weight bias, narration tone.
5. Goal arbiter: urge→goal promotion with hysteresis; persist goals; resume on restart.
6. Deliberation (internal): goal → internal action; execute via the (still-stub) Effectors as internal ops; record outcome → feeds drives + affect + episodic memory.
7. REPL/state additions: show live drive bars, current mood, active goals; `/ws/state` channel.
8. Tests: drive accumulation/threshold, idle-spawns-goal, satisfaction lowers drive, mood affects rate/tone, persistence.

## Tasks

- [x] `TASK-3.1` Migrations + seed: `drive_state` (7 drives), `mood`, `goal` → `/fastapi-engineer`
- [x] `TASK-3.2` Drive engine: decay/accrual per tick, outcome adjustment, urge emission (incl. Energy depletion → sleep signal) → `/fastapi-engineer` [TC-3.1, TC-3.3, TC-3.7]
- [x] `TASK-3.3` ⫘ Affect: appraisal → mood delta + emotion tags + current-mood accessor → `/fastapi-engineer` [TC-3.4]
- [x] `TASK-3.4` Wire affect back into cognition: cycle-rate modulation + attention/recall salience bias + narration tone → `/fastapi-engineer` [TC-3.4]
- [x] `TASK-3.5` Goal arbiter: urge→goal promotion with anti-thrash hysteresis; persist + resume → `/fastapi-engineer` [TC-3.2, TC-3.5]
- [ ] `TASK-3.6` Deliberation (internal actions: reflect/recall/consolidate/formulate-question) + outcome feedback loop → `/fastapi-engineer` [TC-3.2]
- [ ] `TASK-3.7` ⫘ `/ws/state` channel + REPL drive bars / mood / goals view → `/fastapi-engineer`
- [ ] `TASK-3.8` Tests: idle accumulation crosses threshold → goal spawned with no input; satisfying a drive lowers it → `/qa-test-engineer` [TC-3.1, TC-3.2, TC-3.3]
- [ ] `TASK-3.9` ⫘ Tests: mood modulates cycle rate + biases attention; arbitration doesn't oscillate → `/qa-test-engineer` [TC-3.4, TC-3.6]
- [ ] `TASK-3.10` ⫘ Persistence test: drives/mood/goals survive restart; in-flight goal resumes → `/qa-test-engineer` [TC-3.5]
- [ ] `TASK-3.11` ⫘ Affect/appraisal contract test (model output → MoodDelta projection) → `/qa-test-engineer`
- [ ] `TASK-3.12` ⫘ Security review: no runaway resource use from cycle-rate escalation; goal/action loop can't spin unbounded → `/security-reviewer`

## Notes
- Autonomy here is **internal** (reflect/recall/consolidate). Reaching out to the world (web, news, messaging) is Phase 6/8 — do not pull those forward; this phase proves the *motivation* mechanism in isolation.
- After this phase, the demo is: open the REPL, say nothing, and watch Johnny get curious/bored and decide to do something on his own — with a visible mood and rising drives.
