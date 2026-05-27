# Test Plan: Phase 5a — Web API

## Prerequisites
- Phases 0–4 complete; stack up. DB-backed → in-network (`./ctl.sh test`), single runner (lessons.md).
- No frontend yet — verified by pytest + curl-captured fixtures. Browser tests are Phase 5b.

## Test Cases

### TC-5a.1: The token gate protects every `/api/v1` route
**Steps:** With a non-blank `ws_token`, hit each `/api/v1/*` route with no token, a wrong token, and the correct token.
**Expected:** No/wrong token → **401** before any data; correct token → 200. Constant-time compare; token never logged. `/api/health` unauthenticated → bare `200/503` (no per-dependency detail); authenticated → full detail. Blank `ws_token` (dev) → open, as documented.
**Status:** ⬜

### TC-5a.2: `POST /api/v1/input` enqueues a percept (the talk-to-him path)
**Steps:** POST a message; inspect the `InputQueue` (`johnny:sensorium:inputs`) depth; run one Sensorium tick.
**Expected:** Response `{accepted:true, queue_depth>=1}`; the message is on the same Redis queue the REPL uses; the next tick drains it into a `percept` (kind=input) → it flows through appraise/recall/narrate (does NOT bypass the cycle). Blank/oversized input → 422/413, not enqueued. **Queue-depth cap:** when the queue already holds ≥ the bound, POST returns `429` and does not enqueue (a runaway client can't grow the Redis list unboundedly).
**Status:** ⬜

### TC-5a.3: `GET /api/v1/state` matches the `/ws/state` payload shape
**Steps:** Read `/api/v1/state`; compare to a `/ws/state` frame.
**Expected:** Identical shape (drives[], mood, goals[], interval, sleep{asleep,full_agency,last}) — because both serialize via the one extracted function (WS from the tick `ctx`, REST from current state read off the repos). A fresh Johnny (no mood/goals) returns the empty-state shape without nulls-as-crashes. A byte/shape comparison of the REST snapshot vs a captured WS frame confirms no drift.
**Status:** ⬜

### TC-5a.4: Thoughts + audit endpoints project the bus log
**Steps:** Run a few ticks; GET `/api/v1/thoughts` and `/api/v1/audit`.
**Expected:** Thoughts `{thoughts:[{id,ts,text}]}` newest-first, `limit` honoured. Audit `{events:[{id,ts,module,type,payload}]}` includes an `action.dispatched` event (the FC-5 dispatch point) and `drive.*`/`mood`/`state` events; `type` filter works.
**Status:** ⬜

### TC-5a.5: Memory endpoints — episodes (browse+search) + facts (recall)
**Steps:** Seed episodes + semantic facts; GET `/api/v1/memory/episodes` (with and without `q`) and `/api/v1/memory/facts?q=`.
**Expected:** Episodes recent-first with full fields (kind, content, actors, emotion_tags, salience); `q` returns similarity-ranked. Facts return the triple + confidence + `source_episode_ids` provenance.
**Status:** ⬜

### TC-5a.6: Goals, sleeps, self endpoints
**Steps:** Seed an active goal, a sleep_log row, an identity version + a self_improvement_note; GET `/api/v1/goals`, `/sleeps`, `/self`.
**Expected:** Goals `{active, recent}`. Sleeps newest-first with counts + `self_check_ok`. Self `{identity:{name,version,self_model_doc,values,concerns,relationships}, notes:[...]}` — the latest identity version, name from the anchor.
**Status:** ⬜

### TC-5a.7: The app boots with the full surface
**Steps:** `./ctl.sh up`; hit `/api/health`; list routes.
**Expected:** App starts; all 5a routes mounted on `/api/v1`; no import/wiring error; the cognitive loop + WS streams still run (no regression to Phases 2–4).
**Status:** ⬜

### TC-5a.8: Wire fixtures captured (populated AND empty)
**Steps:** Run the fixture-capture test against a seeded stack AND a fresh (empty) stack.
**Expected:** `tests/fixtures/wire/*.json` exists for every endpoint, in BOTH a populated and an empty/fresh-Johnny variant (no rows, never-slept, null mood, seed-only identity v1). These are the literal captures Phase 5b's service adapters are contract-tested against (Step 7c) — not hand-authored.
**Status:** ⬜

### TC-5a.9: No regression — Phases 2–4 still green
**Steps:** Full suite 3× in-network (single runner).
**Expected:** All prior cognition/memory/drives/sleep tests still pass; the new endpoints don't perturb the loop. 3× deterministic.
**Status:** ⬜
