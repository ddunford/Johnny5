# Phase 2: Heartbeat + Global Workspace

## Overview
The first moment Johnny is *alive*. This phase builds the Global Workspace (the event bus all inner agents read/write), the cognitive cycle (the continuous heartbeat), and the first three inner agents needed for a visible inner life: **Sensorium** (turn inputs into percepts), **Attention** (select what's salient → the workspace bottleneck), and the **Inner Narrator** (emit a first-person thought each tick). Memory from Phase 1 is wired in for recall + episodic write. Output is a continuous **stream of consciousness** you can watch in a terminal REPL.

**Done when:** `./ctl.sh up` starts a continuously-ticking cognitive loop that, with no input, produces a coherent first-person monologue referencing recalled memories; feeding it an input (via REPL) visibly shifts attention and the narration on the next tick; every tick's workspace broadcasts are logged and streamable; the loop survives provider hiccups (degrades, doesn't die).

## Custom Feature: Global Workspace + cognitive cycle

**Purpose:** The coordination substrate of the entire society (`SPEC.md §4, §7`). No module exists — this is the core architectural invention. Inner agents are decoupled async tasks communicating only via the workspace.

**Database tables:**
| Table | Key columns | Notes |
|-------|------------|-------|
| `workspace_event` | id, ts, module, type, payload jsonb | the bus log — every broadcast, for observability/replay |
| `thought` | id, ts, text, mood_id (nullable until Phase 3) | the inner monologue stream |
| `percept` | id, ts, modality, raw, normalised jsonb, source | normalised inputs |

**Internal interfaces:**
- `Workspace.broadcast(event)` / `.subscribe(type, handler)` — Redis pub/sub; every broadcast also persisted to `workspace_event`.
- `InnerAgent` protocol — `name`, `subscribes_to`, `async handle(event) -> [Event]`, `prompt`, `model_route`. All agents conform so they can be registered/spawned dynamically (forward-compat with Phase 9).
- `CognitiveCycle.tick()` — one cycle: perceive → appraise(stub) → attend → recall → narrate → deliberate(stub) → check(stub) → act(stub) → learn. Rate-modulated input (fixed this phase; affect-driven in Phase 3).
- `Sensorium.perceive()`, `Attention.select(working_memory, percepts) -> workspace_contents`, `Narrator.narrate(workspace) -> thought`.

**Service modules:** `brain/workspace.py`, `brain/cycle.py`, `brain/agents/{sensorium,attention,narrator}.py`, `repl/` cockpit.

**Key patterns (non-obvious):**
- **Attention is a real bottleneck, not a pass-through.** Per LIDA/GWT research, broadcasting everything degrades decisions — Attention must *select* a bounded salient set into the workspace. This is the single most important design constraint of the phase; resist "just put all percepts in the prompt."
- Deliberation/Conscience/Effectors/Affect are **explicit stubs** this phase (the cycle calls them, they no-op or echo) so the loop shape is correct and Phases 3/6 slot in without restructuring.
- An incoming input is a high-salience percept that raises cycle rate and wins attention — but still flows through the full cycle (appraise→recall→narrate), which is why interaction feels continuous, not stateless.
- The loop must never die on a provider error — wrap tick stages so a failed LLM call degrades that stage (skip narration / simpler thought) and the heartbeat continues.

**Test checklist:** see `test-plan-phase-2.md`.

## Implementation steps
1. Workspace: Redis pub/sub broadcast + subscribe; persist every event to `workspace_event`.
2. `InnerAgent` protocol + a registry that wires subscriptions on startup.
3. Cognitive cycle skeleton: the full tick pipeline with stubbed stages, fixed tick interval, clean start/stop tied to app lifespan.
4. Sensorium: normalise REPL/text input (and a system-metrics percept) into `percept` rows + workspace events.
5. Attention: salience scoring over working memory + new percepts; select bounded set into the workspace (uses Phase 1 working memory).
6. Memory wiring: recall step pulls relevant episodes/facts into the workspace; learn step writes an episode per tick of note.
7. Inner Narrator: first-person thought per tick from current workspace contents; write `thought` + broadcast.
8. REPL cockpit: tail the consciousness stream, dump workspace state, inject an input, step/pause the cycle.
9. WebSocket endpoint `/ws/consciousness` emitting thoughts live (UI consumes it in Phase 5).
10. Frozen-clock cycle harness for deterministic tests.

## Tasks

- [x] `TASK-2.1` Workspace bus: Redis pub/sub broadcast/subscribe + persist to `workspace_event` → `/fastapi-engineer` [TC-2.4]
- [x] `TASK-2.2` `InnerAgent` protocol + startup registry wiring subscriptions → `/fastapi-engineer`
- [ ] `TASK-2.3` Cognitive cycle skeleton (full pipeline, stubbed stages, lifespan-managed start/stop) → `/fastapi-engineer` [TC-2.1]
- [x] `TASK-2.4` ⫘ Migrations: `workspace_event`, `thought`, `percept` → `/fastapi-engineer`
- [ ] `TASK-2.5` Sensorium: normalise text input + system-metrics percepts → `/fastapi-engineer` [TC-2.2]
- [ ] `TASK-2.6` Attention: salience scoring + bounded selection into workspace (the bottleneck) → `/fastapi-engineer` [TC-2.3]
- [ ] `TASK-2.7` Wire Phase 1 memory: recall into workspace + episodic write on the learn step → `/fastapi-engineer` [TC-2.5]
- [ ] `TASK-2.8` Inner Narrator: first-person thought per tick (via router role `narrator` → gemma4) → `/fastapi-engineer` [TC-2.5]
- [ ] `TASK-2.9` REPL cockpit: tail consciousness, dump workspace, inject input, step/pause → `/fastapi-engineer` [TC-2.2, TC-2.6]
- [ ] `TASK-2.10` ⫘ `/ws/consciousness` WebSocket emitting thoughts live → `/fastapi-engineer` [TC-2.7]
- [ ] `TASK-2.11` Frozen-clock cycle harness → `/qa-test-engineer` [TC-2.1]
- [ ] `TASK-2.12` Cycle/attention/narrator tests: deterministic tick, attention bounds, narration references recalled memory, loop survives forced provider failure → `/qa-test-engineer` [TC-2.1, TC-2.3, TC-2.5, TC-2.6]
- [ ] `TASK-2.13` ⫘ Narrator contract test (model output → `thought` projection) → `/qa-test-engineer`
- [ ] `TASK-2.14` ⫘ Security review: WebSocket auth/gate, workspace log has no secrets, REPL access controlled → `/security-reviewer`

## Notes
- Still no web UI — the deliverable is verified via the REPL + `/ws/consciousness` + pytest. Playwright arrives in Phase 5.
- This phase is the demo milestone: a terminal showing Johnny thinking to himself, continuously, and reacting when spoken to.
