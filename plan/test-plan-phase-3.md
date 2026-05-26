# Test Plan: Phase 3 — Drives + Affect

## Prerequisites
- Phases 0–2 complete; heartbeat running; `inference.lan` reachable.

## Test Cases

### TC-3.1: Drives accumulate while idle
**Steps:** Start the loop; provide no input; sample `drive_state` over time (use frozen-clock to fast-forward).
**Expected:** Curiosity and Boredom rise monotonically toward threshold per their accrual rates; values persist to `drive_state`.
**Status:** ⬜

### TC-3.2: Idle drive spawns a goal and changes behaviour
**Steps:** Continue idle until Curiosity crosses threshold.
**Expected:** An urge is emitted; the arbiter promotes it to a `goal`; Deliberation selects an internal action (e.g. reflect/recall); narration reflects the new intent — all with zero input. Goal outcome recorded.
**Status:** ⬜

### TC-3.3: Satisfaction lowers a drive
**Steps:** With Connection high, inject an interaction; with Curiosity high, let a learn/reflect action complete.
**Expected:** Connection drops after interaction; Curiosity drops after the learning action. Drives move toward setpoint on satisfaction.
**Status:** ⬜

### TC-3.4: Mood colours cognition
**Steps:** Inject a goal-congruent positive event, then a frustrating one (repeated failed action).
**Expected:** `mood` valence/arousal shift accordingly; cycle rate increases with arousal; narration tone changes; attention salience bias observable. `thought.mood_id` is populated.
**Status:** ⬜

### TC-3.5: Persistence + goal resumption
**Steps:** With an active goal and non-default drive/mood state, restart the stack.
**Expected:** Drives, mood, and the in-flight goal restore; Johnny resumes the goal rather than starting blank.
**Status:** ⬜

### TC-3.6: Arbitration does not thrash
**Steps:** Drive two drives above threshold near-simultaneously.
**Expected:** One goal is selected and pursued for a stable stretch (hysteresis); no per-tick flip-flopping between competing goals.
**Status:** ⬜

### TC-3.7: Energy trends toward sleep signal
**Steps:** Run sustained high-activity ticks.
**Expected:** Energy depletes and emits a sleep-needed signal (consumed for real in Phase 4); cycle rate slows as energy falls.
**Status:** ⬜
