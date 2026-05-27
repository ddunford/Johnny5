# Phase 5a: Web API (the surface the UI consumes)

## Overview
Phase 5 is the browser — but a React SPA is only as honest as the API it reads. The cognitive loop currently exposes **two WebSocket streams** (`/ws/consciousness`, `/ws/state`) and an **empty `/api/v1` seam** (`johnny/api/v1/router.py` — "the seam exists, the routes don't yet"). External input reaches Johnny **only** via the REPL pushing to a Redis `InputQueue` (`johnny:sensorium:inputs`, drained by Sensorium each tick); there is **no HTTP way to talk to him** and **no read endpoint** for his memory/goals/audit/self. And only `/ws/*` is token-gated — `/api/*` is wide open.

Phase 5a builds that API: the **input endpoint** (the "talk to him" send), the **read endpoints** the panels need (state snapshot, thoughts, memory, goals, audit, sleeps, self), and the **HTTP auth gate** (the carried Phase-0 advisory). It ships **captured wire fixtures** for every endpoint so Phase 5b's frontend service adapters can be contract-pinned against the real shapes (the canonical frontend↔backend failure mode — see `/plan-review` Step 7c). No frontend in 5a; it's verified by pytest + curl-captured fixtures.

**Done when:** a single-token gate protects `/api/v1/*` (and `/ws/*` stays gated); `POST /api/v1/input` pushes a message onto the same `InputQueue` Sensorium drains (so a tick later it becomes a percept → thought on `/ws/consciousness`); `GET` endpoints return the current state snapshot, recent thoughts, episodic + semantic memory (browse + search), goals, the audit/bus log (incl. dispatched actions), sleep history, and the self-model + reflections — each with a stable, documented envelope; every endpoint has a pytest test (auth reject/admit + shape) and a captured JSON fixture under `tests/fixtures/wire/`.

## Forward-commitment touchpoints
- **FC-8 — the WS streams are the source of truth for *live* state; these REST endpoints are for initial load + history.** `/api/v1/state` returns a snapshot equal in shape to a `/ws/state` frame's payload so the SPA can render immediately on load, then switch to the live socket. Don't fork the schema — reuse the same projection (`brain/cycle.py`'s state payload builder) so REST and WS can't drift.
- **FC-4 / FC-5 — the audit endpoint reads the existing `workspace_event` bus log** (every broadcast, incl. the `action.dispatched` FC-5 dispatch point). No new audit store — project the existing one.
- **FC-1 / FC-9 — read-only.** These endpoints expose the Mind's state for observation; none mutate the Core, and `POST /input` only enqueues a percept (it does not bypass the cycle — Johnny still appraises/recalls/narrates it, `SPEC §7`). Self-edit *approval* actions are Phase 9; the self endpoint is read-only here.

## Custom Feature: the web API

**Purpose:** Expose the running Mind for a browser to watch + talk to (`SPEC §11.1`). The loop already produces everything; 5a is the read/write doorway + the auth gate. No new domain logic, no new tables — it wraps existing repositories + the InputQueue.

**Database tables:** none new. Reads existing: `thought`/`workspace_event`, `episode`, `semantic_fact`/`semantic_edge`, `goal`, `mood`, `sleep_log`, `identity`, `self_improvement_note`.

**API endpoints (all under `/api/v1`, all behind the token gate):**
| Method | Path | Backed by | Returns |
|--------|------|-----------|---------|
| POST | `/api/v1/input` | `InputQueue.push` (sensorium) | `{accepted: true, queue_depth}` — enqueues a percept; reply streams on `/ws/consciousness` |
| GET | `/api/v1/state` | `cycle` state projection | snapshot == a `/ws/state` payload (drives[], mood, goals[], interval, sleep{asleep,full_agency,last}) |
| GET | `/api/v1/thoughts?limit=` | `workspace.recent_events(type=thought)` | `{thoughts:[{id,ts,text}]}` |
| GET | `/api/v1/memory/episodes?limit=&q=` | `EpisodeRepository.recent` / `EpisodicMemory.recall` | `{episodes:[{id,ts,kind,content,actors,emotion_tags,salience}]}` |
| GET | `/api/v1/memory/facts?q=&limit=` | `SemanticMemory.recall` | `{facts:[{id,subject,predicate,object,confidence,source_episode_ids}]}` |
| GET | `/api/v1/goals` | `GoalStore.active` + recent | `{active:[Goal], recent:[Goal]}` |
| GET | `/api/v1/audit?limit=&type=` | `workspace.recent_events` | `{events:[{id,ts,module,type,payload}]}` (incl. `action.dispatched`) |
| GET | `/api/v1/sleeps?limit=` | `SleepLogRepository` | `{sleeps:[SleepLog]}` |
| GET | `/api/v1/self` | `IdentityStore.latest` + `MetacognitionStore.recent` | `{identity:{name,version,self_model_doc,values,concerns,relationships}, notes:[{ts,observation,proposal,status}]}` |

`GET /api/health` stays, but unauthenticated callers get a bare `200/503` (no per-dependency topology) — the Phase-0 advisory; authenticated callers keep the detail.

**Internal interfaces / patterns:**
- **One shared-token HTTP gate** — a FastAPI dependency reusing `settings.ws_token` (the interim single-token model; CLAUDE.md "single-token / Traefik basic-auth gate, no user system"). Constant-time compare; token via `Authorization: Bearer`/`X-Token` header; blank token = dev-open (mirrors the WS gate). Applied to the `/api/v1` router. (A later phase can swap to a session/basic-auth gate behind the same dependency.)
- **Pydantic response envelopes** per endpoint (typed, not raw dicts) — these ARE the contract the SPA pins against. Each is the projection of an existing repository row.
- **The `/api/v1/state` projection is the SAME builder as `/ws/state`** — extract `brain/cycle.py`'s state-payload construction into a shared function both call, so REST snapshot and WS frame are identical by construction (not by hand-copy).
- **Captured wire fixtures:** a test writes each endpoint's real JSON response to `tests/fixtures/wire/*.json` (committed) — Phase 5b's adapters are contract-tested against these literal captures, not hand-authored objects (Step 7c).

**Test checklist:** see `test-plan-phase-5a.md`.

## Implementation steps
1. HTTP token gate dependency (reuse `ws_token`), applied to `/api/v1`; `/api/health` redaction for unauthenticated.
2. `POST /api/v1/input` → `InputQueue.push(text, source="web")`; returns queue depth.
3. Extract the shared state-payload builder from `brain/cycle.py`; `GET /api/v1/state`.
4. Read endpoints: thoughts, memory/episodes (+search), memory/facts, goals, audit, sleeps, self — each a typed envelope over an existing repo.
5. Mount everything on `v1_router`; wire repos via the runtime.
6. Backend tests (auth reject/admit + shape per endpoint; input round-trips to the queue) + capture wire fixtures.

## Tasks

- [x] `TASK-5a.1` HTTP shared-token gate dependency (reuse `ws_token`, constant-time, header-based, blank=dev-open) applied to `/api/v1`; redact `/api/health` for unauthenticated callers (Phase-0 advisory) → `/fastapi-engineer` [TC-5a.1]
- [x] `TASK-5a.2` `POST /api/v1/input` → `InputQueue.push(source="web")` + `{accepted, queue_depth}`; rejects blank/oversized input (422/413) AND **caps queue depth** — reject with `429` when `InputQueue.depth()` exceeds a bound (settings, default ~100) so a runaway client/loop can't grow the Redis list unboundedly → `/fastapi-engineer` [TC-5a.2]
- [x] `TASK-5a.3` Extract the shared state-payload **serializer** from `brain/cycle.py` (the dict-builder that turns drives/mood/goals/sleep into the frame shape — WS uses it from the tick `ctx`); `GET /api/v1/state` builds the snapshot from **current state read off the repos** (`drives.current()` / `affect.current()` / `goals.active()` / `sleep.latest_sleep()`) and serializes it with that SAME function — so REST and WS shapes can't drift (don't fake a tick ctx, don't duplicate the serializer) → `/fastapi-engineer` [TC-5a.3]
- [x] `TASK-5a.4` ⫘ `GET /api/v1/thoughts` + `GET /api/v1/audit` (over `workspace.recent_events`; audit includes `action.dispatched`) → `/fastapi-engineer` [TC-5a.4]
- [x] `TASK-5a.5` ⫘ `GET /api/v1/memory/episodes` (recent + `q` search) + `GET /api/v1/memory/facts` (semantic recall) → `/fastapi-engineer` [TC-5a.5]
- [x] `TASK-5a.6` ⫘ `GET /api/v1/goals` + `GET /api/v1/sleeps` + `GET /api/v1/self` (identity + metacognition notes) → `/fastapi-engineer` [TC-5a.6]
- [x] `TASK-5a.7` Wire `v1_router` sub-routers + repos through the runtime; confirm app boots with the full surface → `/fastapi-engineer` [TC-5a.7]
- [ ] `TASK-5a.8` ⫘ Backend tests: token gate rejects (401) + admits per endpoint; `/input` round-trips to the InputQueue; each read endpoint's shape over seeded rows → `/qa-test-engineer` [TC-5a.1..5a.6]
- [ ] `TASK-5a.9` ⫘ Capture wire fixtures: write each endpoint's real JSON response to `tests/fixtures/wire/*.json` (incl. an EMPTY-state capture per endpoint — no rows / never-slept / null mood) for Phase 5b contract pinning → `/qa-test-engineer` [TC-5a.8]
- [ ] `TASK-5a.10` ⫘ Security review: the token gate actually protects every `/api/v1` route (no unauthenticated leak); `/input` can't inject control commands or oversized payloads; no secrets in any envelope or the audit log; health redaction holds → `/security-reviewer` [TC-5a.1]

## Notes
- **Empty-state fixtures are mandatory** (TASK-5a.9): a fresh Johnny has no episodes, never slept, null mood. The SPA's first-load is exactly this state — capturing it now is what lets 5b's contract tests + fresh-load smoke catch `undefined`/null projection bugs before they ship (Step 7c).
- `/input` does NOT expose pause/step/resume — those stay on the token-gated control channel (`brain/cycle_control.py`); a UI "pause Johnny" control can wrap `send_control` in 5b, or 5a can add `POST /api/v1/control` if 5b needs it (decide at 5b design).
