# Test Plan: Phase 5b — Web UI

## Prerequisites
- Phase 5a complete (API + token gate + captured wire fixtures under `tests/fixtures/wire/`); stack up.
- Frontend tests: Vitest (component/contract) on host; Playwright E2E + the fresh-load smoke against an **actually-running stack with real backend responses** (NOT mocked routes). Playwright `outputDir` kept out of repo root (gitignored).

## Test Cases

### TC-5b.1: Conversation round-trip (talk to him)
**Steps:** Type a message in Conversation, submit. Watch the consciousness stream.
**Expected:** Send shows "heard you" (optimistic); the message reaches Johnny (a `percept` kind=input); within a few ticks a thought referencing it appears in the live consciousness feed. No hung "awaiting reply" spinner — the reply is a thought, not a synchronous response. Empty/oversized message → client validation (RHF+Zod), no send.
**Status:** ⬜

### TC-5b.2: Live consciousness stream
**Steps:** Open the Consciousness panel (browser, real backend).
**Expected:** Recent thoughts backfill on connect; new thoughts stream live; auto-scroll; a dropped socket auto-reconnects + resumes. No console errors.
**Status:** ⬜

### TC-5b.3: State dashboard reflects live state incl. sleep/agency
**Steps:** Open the State dashboard; let drives climb; trigger a sleep.
**Expected:** 7 drive bars (with threshold markers + over-flag), mood (valence/arousal/emotions/descriptor), active goal, heartbeat interval all update live from `/ws/state`; awake→asleep→awake transition shows; **⚠ DEGRADED** shows when `full_agency=false`; self-model version + last-sleep summary render.
**Status:** ⬜

### TC-5b.4: Memory browser (episodic + semantic)
**Steps:** Open Memory; browse episodes; search episodes by `q`; search semantic facts.
**Expected:** Episodes render (kind, content, time, salience); search returns ranked results; facts show triple + confidence + provenance. Empty memory → a friendly empty state, not a blank/crash.
**Status:** ⬜

### TC-5b.5: Audit / actions panel
**Steps:** Open Audit; filter by event type.
**Expected:** The bus log renders (module, type, ts, payload); `action.dispatched` events are visible/highlighted; type filter works.
**Status:** ⬜

### TC-5b.6: Self panel
**Steps:** Open Self.
**Expected:** Current identity (name, version, self_model_doc, values, concerns, relationships) + latest metacognitive reflections/proposals render read-only. The Phase-9 approval area is a clearly-labelled placeholder (not a fake control). Fresh Johnny (seed-only identity v1, no notes) renders cleanly.
**Status:** ⬜

### TC-5b.7: Auth gate (UI side)
**Steps:** Load the SPA with no token; with a wrong token; with the correct token. Force a WS 1008 / a REST 401.
**Expected:** No/wrong token → token-entry gate, NO panel loads any data; correct token → app loads, header sent on REST, `?token=`/header on WS. A 1008 (bad WS token) or 401 routes back to the gate. Token in sessionStorage (not localStorage), never logged or put in a REST URL.
**Status:** ⬜

### TC-5b.8: Service contract tests (Step 7c — the load-bearing one)
**Steps:** Feed each `services/*.ts` adapter the literal captured wire fixture from `tests/fixtures/wire/*.json` (5a TASK-5a.9) — both populated AND empty-state.
**Expected:** Each adapter projects the real server envelope correctly; the empty-state fixture (no rows, never-slept, null mood, seed-only identity) projects without `undefined`/null errors. A deliberately-mismatched fixture fails the test (proves it pins the real shape, not a hand-authored wishlist). Every shipped service adapter has one.
**Status:** ⬜

### TC-5b.9: Served behind Traefik
**Steps:** `./ctl.sh up`; load `https://johnny.demosrv.uk`; check `/api/*` + `/ws/*` route to the backend.
**Expected:** The SPA loads over TLS (certresolver `le`); `/` → web (nginx, Vite build); `/api` + `/ws` → api (path rules + priorities win over the SPA catch-all); WS upgrades work through Traefik.
**Status:** ⬜

### TC-5b.10: Fresh-load smoke (MANDATORY — real backend, every panel)
**Steps:** Against a freshly-started stack (fresh Johnny: no thoughts, never slept, null mood, seed-only identity v1) with a real backend (NOT mocked routes): enter the token, then navigate to EVERY panel (Conversation, Consciousness, State, Memory, Audit, Self).
**Expected:** Every panel renders without a blank screen, **zero browser-console errors**, no `Cannot read properties of undefined` / `Cannot convert undefined or null to object`. This is the bug class contract tests + mocked component tests can't catch — it must run against real empty-state responses.
**Status:** ⬜

### TC-5b.11: No backend regression
**Steps:** Full backend suite 3× in-network after the 5b wiring (single runner).
**Expected:** Phases 2–5a still green; serving the SPA + the `web` service don't perturb the loop or the API.
**Status:** ⬜
