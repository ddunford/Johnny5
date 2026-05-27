# Phase 5b: Web UI (watch and talk to him in a browser)

## Overview
The first time Johnny becomes *visible*. Phase 5b builds the React + Vite SPA that consumes Phase 5a's API + the two WebSocket streams: a **Conversation** to talk to him, a live **Stream-of-consciousness**, a **State dashboard** (drive bars, mood, goals, energy, awake/asleep, self-model version, last sleep), a **Memory browser**, an **Audit** view of the bus/actions, and a read-only **Self** panel (identity doc + metacognitive reflections). All four SPEC §11 "views onto the same continuously-running being" — none of them start Johnny; they attach to him.

**Done when:** behind the token gate at `johnny.demosrv.uk`, you can watch his consciousness stream live, see his drives climb / mood shift / a goal appear / him fall asleep and wake on the State dashboard (all from `/ws/state`), type a message and watch it become a percept he thinks about and responds to, browse his episodic + semantic memory, scan the audit log + dispatched actions, and read his current self-model + latest reflection — every panel rendering correctly against a **real running backend** (not mocks), including the fresh/empty-Johnny state.

## Forward-commitment touchpoints
- **FC-8 — the SPA is a consumer, not the source of truth.** It renders the headless loop's WS frames + REST snapshots; closing the tab doesn't stop Johnny. Initial load uses the REST snapshot (`/api/v1/state`, recent thoughts), then the WS streams take over for live updates.
- **Contract pinning (house rule + `/plan-review` Step 7c) — non-negotiable this phase.** Every `services/*.ts` that wraps an API/WS response declares a `ServerEnvelope` type modelling 5a's ACTUAL output and has a contract test fed a **literal fixture captured from the wire** (`tests/fixtures/wire/*.json` from TASK-5a.9), incl. the **empty-state** capture. Page/component tests that mock the service prove rendering, NOT shape — the service contract test is the only thing that proves the shape matches the server.

## Architecture (per CLAUDE.md Frontend Patterns)
- **React 19 + Vite + TypeScript**, feature-based structure (`features/{conversation,consciousness,state,memory,audit,self}`).
- **React Query** for request/response reads (thoughts/memory/goals/audit/sleeps/self); **Zustand** for the live streamed state (consciousness feed, state dashboard) fed by the WS clients.
- **Service classes** wrap the API + WS (typed envelopes + adapters), consumed by hooks. **React Hook Form + Zod** for the conversation send + token entry.
- **Error boundary per panel** + a global fallback (a dead panel must not kill the live consciousness view — CLAUDE.md).
- **Token gate:** the SPA prompts for the shared token, stores it (sessionStorage), and sends it as the `Authorization`/`X-Token` header on REST + the `?token=`/header on the WS connects.

## Custom Feature: the SPA + its service layer

**Database tables:** none (consumes 5a).

**Service modules (`frontend/src/services/`) — each typed + contract-tested:**
| Service | Wraps | ServerEnvelope source |
|---------|-------|----------------------|
| `consciousnessSocket` | `/ws/consciousness` | thought frame `{type,id,ts,text}` |
| `stateSocket` | `/ws/state` | state frame (drives[],mood,goals[],interval,sleep{}) |
| `conversation` | `POST /api/v1/input` | `{accepted,queue_depth}` |
| `stateApi` | `GET /api/v1/state` | snapshot (== state frame) |
| `memoryApi` | `/api/v1/memory/{episodes,facts}` | episode + fact envelopes |
| `goalsApi` / `sleepsApi` | `/api/v1/{goals,sleeps}` | goal + sleep_log envelopes |
| `auditApi` | `/api/v1/audit` | bus-event envelope |
| `selfApi` | `/api/v1/self` | identity + notes envelope |

**Panels (`features/`):**
- **Conversation** — message input (RHF+Zod) → `conversation.send` → optimistic "sent"; his reply surfaces as thoughts on the consciousness stream (no separate reply endpoint — he *thinks about* your message, `SPEC §7`). Show the round-trip.
- **Consciousness** — live first-person thought feed from `consciousnessSocket` (Zustand), backfill on connect, auto-scroll, reconnect.
- **State dashboard** — `stateSocket` (Zustand): 7 drive bars with threshold markers + over-flag, mood (valence/arousal/emotions/descriptor), active goal, heartbeat interval, **awake/asleep + ⚠ DEGRADED (full_agency=false) + self-model version + last-sleep summary**.
- **Memory browser** — episodic (recent + search box) + semantic facts (search), via React Query.
- **Audit** — the bus/event log (filter by type), action.dispatched highlighted.
- **Self** — identity doc (name, version, values, concerns, relationships) + latest metacognitive reflections/proposals. Read-only (self-edit approval UI is Phase 9 — leave a labelled placeholder, don't fake it).

**Serving / Traefik:** a new `web` service (nginx serving the Vite production build) on `traefik_demosrv`; Traefik routes `Host(johnny.demosrv.uk)` `/` → `web`, and `/api` + `/ws` → the existing `api` service (PathPrefix rules + priorities so the API/WS paths win over the SPA catch-all). `ctl.sh` builds the web image + brings it up. Dev: Vite dev server proxying `/api`+`/ws` to the api container.

**Key patterns (non-obvious):**
- **The conversation has no reply endpoint by design** — you speak, he perceives, and his response emerges in his stream-of-consciousness (continuity, not request/response). The UI correlates by timestamp, it doesn't await a synchronous answer.
- **WS reconnect + token-reject UX:** a 1008 close (bad token) routes to the token-entry gate; a transient drop auto-reconnects with backfill.
- **Empty-state first paint:** every panel must render the fresh-Johnny shape (no thoughts yet, never slept, null mood, seed-only identity) without blanking or `undefined` errors — the contract tests use 5a's empty-state fixtures specifically for this.

**Test checklist:** see `test-plan-phase-5b.md`.

## Implementation steps
1. Scaffold React 19 + Vite + TS (`frontend/`); app shell, routing, token-gate entry, React Query + Zustand providers, per-panel error boundaries.
2. Service layer: typed envelopes + adapters for every API/WS surface; the WS clients (reconnect+backfill).
3. Contract tests for every service adapter, fed 5a's captured wire fixtures (populated + empty).
4. Panels: Conversation, Consciousness, State dashboard, Memory, Audit, Self — each consuming its service via hooks, with an error boundary.
5. `web` nginx service + Traefik path routing; `ctl.sh` build/up; Vite dev proxy.
6. Vitest component tests; Playwright E2E per panel + the conversation round-trip + the token-gated fresh-load smoke against a real backend.

## Tasks

- [x] `TASK-5b.1` Scaffold `frontend/` (React 19 + Vite + TS), app shell + routing + React Query/Zustand providers + per-panel error boundary + global fallback → `/frontend-react-architect` [TC-5b.9, TC-5b.10]
- [x] `TASK-5b.2` Token gate: entry form (RHF+Zod), sessionStorage, header injection on the API client, `?token=` on WS; 1008/401 → re-prompt → `/frontend-react-architect` [TC-5b.7]
- [x] `TASK-5b.3a` API client core — fetch wrapper + token-header injection + 401→re-gate handling + the React Query provider/setup + the shared `ServerEnvelope` typing convention (each adapter cites its 5a endpoint) → `/frontend-react-architect` [TC-5b.7, TC-5b.8]
- [x] `TASK-5b.3b` ⫘ Typed read adapters + React Query hooks over 5b.3a — state, thoughts, memory (episodes+facts), goals, audit, sleeps, self + the conversation `send` (POST /input); each adapter a typed projection of 5a's envelope → `/frontend-react-architect` [TC-5b.8]
- [ ] `TASK-5b.4` ⫘ WS service clients (`consciousnessSocket`, `stateSocket`) → Zustand stores; backfill-on-connect + auto-reconnect → `/frontend-react-architect` [TC-5b.2, TC-5b.3]
- [ ] `TASK-5b.5` **Service contract tests** — every adapter fed 5a's literal captured wire fixtures (`tests/fixtures/wire/*.json`), populated AND empty-state; assert the projection (Step 7c) → `/qa-test-engineer` [TC-5b.8]
- [ ] `TASK-5b.6` ⫘ Conversation panel — message send (RHF+Zod) → `conversation.send` → optimistic + correlate the reply on the consciousness stream → `/frontend-react-architect` [TC-5b.1]
- [ ] `TASK-5b.7` ⫘ Consciousness panel — live thought feed (Zustand), backfill, auto-scroll, reconnect → `/frontend-react-architect` [TC-5b.2]
- [ ] `TASK-5b.8` ⫘ State dashboard — drive bars + mood + goal + interval + awake/asleep + ⚠DEGRADED + self-model version + last-sleep (from `stateSocket`) → `/frontend-react-architect` [TC-5b.3]
- [ ] `TASK-5b.9a` ⫘ Memory browser panel (episodic recent + search, semantic facts) → `/frontend-react-architect` [TC-5b.4]
- [ ] `TASK-5b.9b` ⫘ Audit panel (bus/event log, `action.dispatched` highlighted, type filter) → `/frontend-react-architect` [TC-5b.5]
- [ ] `TASK-5b.10` ⫘ Self panel (identity doc + values/concerns/relationships + latest reflections; read-only, labelled Phase-9 placeholder for approvals) → `/frontend-react-architect` [TC-5b.6]
- [x] `TASK-5b.11` `web` nginx service serving the Vite build + Traefik routing (`/`→web, `/api`+`/ws`→api, priorities); `ctl.sh` build/up; Vite dev proxy → `/devops-deployment-engineer` [TC-5b.9]
- [ ] `TASK-5b.12` ⫘ Vitest component tests (panels render given mocked hooks; error boundary catches a thrown panel; empty-state renders) → `/frontend-react-architect` [TC-5b.1..5b.6]
- [ ] `TASK-5b.13` ⫘ Playwright E2E: per-panel render + the **conversation round-trip** (type → percept → a thought returns) + navigation + WS reconnect + token-reject → re-prompt → `/qa-test-engineer` [TC-5b.1, TC-5b.2, TC-5b.7]
- [ ] `TASK-5b.14` **Fresh-load smoke (MANDATORY)** — against a real running stack (NOT mocked routes): enter the token, then load EVERY panel; assert no blank screen, zero console errors, no `Cannot read properties of undefined`/`...null` on the fresh/empty-Johnny state → `/qa-test-engineer` [TC-5b.10]
- [ ] `TASK-5b.15` ⫘ Security review: token gate enforced on every API + WS call (no unauthenticated panel loads data); token in sessionStorage not localStorage + never logged/URL-leaked for REST; XSS-safe rendering of thought/memory/identity text (no `dangerouslySetInnerHTML`); CSP/headers on the nginx service → `/security-reviewer` [TC-5b.7]

## Notes
- **The conversation is not request/response** — don't build a "wait for reply" spinner that hangs; the reply is a thought in his stream. The panel shows "he heard you" + surfaces the correlated thoughts.
- Self-edit *approval* UI (approve/reject pending code edits) is **Phase 9** — the Self panel is read-only here with a clearly-labelled placeholder; do not stub a fake approval flow.
- Playwright artifacts/`outputDir` stay out of repo root (CLAUDE.md Playwright hygiene); E2E config keeps `test-results/` gitignored.

## Carried-over advisories
- **Phase-0 (LOW), now resolved upstream:** `/api/health` topology redaction + the `/api/v1` + `/ws/*` auth gate land in **5a**; 5b's job is to drive that gate from the UI (token entry + 401/1008 handling). Confirm in TC-5b.7 that an unauthenticated SPA gets nothing.
