# Phase 6a: Safe-action substrate (Conscience + dispatch + audit + the carried safety items)

## Overview
Until now Johnny only acts *internally* (Phase 3 Deliberation: reflect / recall / consolidate / formulate-a-question). Phase 6 lets him act on the **world** — but the world is where he can do harm or rack up cost, so the **safe-action substrate lands before any world-touching tool**. 6a builds the machinery that makes every external action *checked, bounded, and audited*; 6b plugs the actual tools (web/news/code/notes/scheduler/memory) into it.

The cycle already has the seams (FC-7): the **CHECK** stage is a stub explicitly reserved for the **Conscience** (`SPEC §5 #14`), and **ACT** routes through the single **`_dispatch_action`** point (FC-5) that today just broadcasts intent. 6a fills CHECK with a real Conscience, makes ACT run an approved tool through a typed Effector registry, and makes the dispatch point write an immutable **`action_log`** (the `SPEC §9.3` audit safeguard). It also lands the **three carried advisories** that this phase's autonomy makes mandatory: the **BudgetGovernor hard pre-call gate** (P3), **`/no_think`** for the local reasoning model (P4), and **no-secrets-on-the-bus** (P5a).

**Done when:** a proposed action is a typed `(tool, args)` the Conscience vets against Johnny's values (allow / veto + reason — pure values, fully Johnny-editable, no un-loosenable floor) before any Effector runs; an approved action executes through the tool registry and is written to `action_log` (tool, args, result, conscience_verdict, goal_id) via the Core's append-only audit writer + surfaced on `/api/v1/audit`; a vetoed action never runs and is logged with its reason; the `BudgetGovernor` actually **blocks/degrades** a cloud LLM call when the daily budget is exhausted (and `deliberation` is back to cloud-first now it's safe); the local reasoning model emits clean structured JSON via `/no_think`; and no secret can reach a `workspace_event`/`action_log` payload. **No real tools yet** — 6a ships one inert built-in test Effector to exercise the pipeline; 6b adds the world-touching ones.

## Forward-commitment touchpoints
- **FC-5 — the single dispatch + audit point becomes real.** Every Effector action (internal *and* external) goes through `_dispatch_action`, which now: runs the Conscience check, executes the approved tool via the registry, and writes `action_log`. One seam, uniformly checked + audited — no tool may bypass it.
- **FC-9 — the Conscience is the Mind's, fully Johnny-editable; harm-prevention is Core *mechanism*, not a floor inside the Conscience.** The Conscience judges *values* ("should I?") — entirely Johnny's, prompt-backed (FC-3), with **no un-loosenable denylist baked in** (a floor the Mind can't edit would itself violate FC-9). What stops a permissive/empty/buggy Conscience from causing harm is NOT a values-floor — it's the **Core mechanisms** that apply *regardless of the verdict*: the budget gate (6a), the append-only audit (6a), and in 6b the sandbox + SSRF guard, plus the always-on kill switch. Two independent layers: the Mind's Conscience ("should I?", editable) and the Core's hard limits ("can I, safely?", mechanism, un-disableable). Do NOT put values-judgement in `core/`, and do NOT put host-safety limits inside the Conscience.
- **FC-4 — the BudgetGovernor finally enforces.** `core/governors.py`'s `BudgetGovernor` exists but is never consulted (P3 finding). 6a wires `over_budget()`/`would_exceed()` into `LLMRouter.complete()` as a hard pre-call gate: an exhausted budget skips the cloud step → local fallback (or refuses). THEN `deliberation` returns to cloud-first in `llm_routes.toml` (the P3 mitigation was local-first *because* the gate didn't exist — now it does).
- **FC-1 — the Core audit writer is import-isolated + append-only.** `core/audit.py` writes `action_log` and is read-only to the Mind (the Mind can't rewrite or delete its own audit trail). It aggregates by table name, never imports `brain/` (mirrors `governors.py`).
- **FC-7 — fill CHECK + ACT in place.** Don't restructure the tick pipeline; the Conscience slots into the named CHECK stage, the Effector run into ACT.

## Custom Feature: the safe-action substrate

**Database tables:**
| Table | Key columns | Notes |
|-------|------------|-------|
| `action_log` | id, ts, tool, args jsonb, result jsonb, conscience_verdict, veto_reason, goal_id, success | the audit trail (`SPEC §12`); append-only; one row per dispatched (or vetoed) action; surfaced on `/api/v1/audit` (already wired) + streamed to the UI |

**Internal interfaces:**
- `Tool` protocol — `name`, `args_schema` (Pydantic), `danger` class (e.g. `safe`/`network`/`exec`/`public`), `async run(args) -> ToolResult`. Registered in a `ToolRegistry` (the Effector belt; dynamic add/retire like `InnerAgent`, forward-compat with P9 self-ops). 6a ships a single inert `noop`/`echo` tool to exercise the pipeline.
- `Conscience.vet(action, *, values, workspace) -> Verdict` — judges a proposed `(tool, args)` against **Johnny's values only** (git-backed prompt, FC-3 — fully editable, no baked-in denylist the Mind can't loosen; a values-floor in here would itself break FC-9). Returns allow / veto + reason. Router role `conscience` (local/fast — `SPEC §10`). Pure projection `parse_verdict` for the contract test. *Harm-to-host/self is NOT the Conscience's job* — that's enforced independently by the Core mechanisms (budget gate, append-only audit, + 6b's sandbox/SSRF), which hold regardless of the verdict, so a permissive/empty/buggy Conscience still can't cause host harm.
- `EffectorDispatch` (the real `_dispatch_action`) — `propose(action) → vet (Conscience) → if allowed: run via registry → write action_log → emit outcome; if vetoed: write action_log(veto) + emit, do not run`. The single audited path (FC-5). Bounded: one action per tick (carried from P3).
- `core/audit.py` `AuditWriter.record(entry)` — append-only `action_log` writer, import-isolated (FC-1).
- `LLMRouter.complete(...)` — now consults `BudgetGovernor` before a *cloud* step: if today's spend ≥ budget, skip cloud → next chain step (local) → or `LLMUnavailableError` if no fallback. Logged as a `budget_skip`.
- `OllamaProvider` — sends the ollama `think:false` option (or equivalent) for the **reasoning** model on **schema** (json_object) roles, so qwen emits JSON without the reasoning preamble (P4 carried).

**Key patterns (non-obvious):**
- **Vet BEFORE run, always, on the one path.** There is no code path from a goal to a tool that doesn't pass through `EffectorDispatch` → Conscience. A new tool (6b) is automatically vetted + audited because it can only run via the registry behind the dispatch point.
- **The Conscience is values, the Core is harm-to-self/host.** Two layers: the Mind's Conscience (editable, "should I?") + the Core's hard limits (budget/sandbox/audit, "can I, safely?"). A `danger:public`/`danger:exec` tool can be additionally gated by a Core/human approval in 6b/later, but the *content* judgement is the Conscience's.
- **The audit log is append-only + the Mind can't erase it** (FC-1) — it's how "total observability" (`SPEC §9.3`) survives even a misbehaving Mind. `action_log` writes go through `core/audit.py`; the Mind has no delete path.
- **Budget gate is a pre-call skip, not a crash** — an exhausted budget degrades to local ("tired"), it doesn't kill the cycle (the SPEC §10 graceful-degradation posture).
- **No secret reaches the bus.** Tool results (esp. 6b's web-fetch + later messaging) can carry tokens/keys/credentials from fetched content or config; a redaction/allowlist guard sits on the `workspace_event` + `action_log` write path so `/api/v1/audit` (world-readable to a token-holder) never leaks one.

**Test checklist:** see `test-plan-phase-6a.md`.

## Implementation steps
1. Migration: `action_log`.
2. `Tool` protocol + `ToolRegistry` + one inert built-in tool (pipeline exerciser).
3. Conscience agent (vet → verdict) + `conscience` role in `llm_routes.toml` + `config/prompts/conscience.md`; fill the CHECK stage.
4. `EffectorDispatch`: vet→run→audit on the FC-5 point; wire into ACT; Deliberation can propose a `(tool,args)` action (the inert tool this phase).
5. `core/audit.py` append-only writer; route `action_log` through it.
6. BudgetGovernor hard pre-call gate in `LLMRouter.complete()`; revert `deliberation` to cloud-first.
7. `/no_think` for the reasoning model on schema roles in `OllamaProvider`; re-verify the sleep `@live` legs go green locally.
8. No-secrets-on-the-bus redaction guard on the broadcast + action_log write path.
9. Tests + security review.

## Tasks
- [ ] `TASK-6a.1` Migration: `action_log` (tool, args, result, conscience_verdict, veto_reason, goal_id, success, ts) → `/fastapi-engineer` [TC-6a.4]
- [ ] `TASK-6a.2` `Tool` protocol + `ToolRegistry` (typed args schema + danger class, dynamic register/retire) + one inert built-in `noop` tool → `/fastapi-engineer` [TC-6a.1]
- [ ] `TASK-6a.3` Conscience agent: `vet((tool,args)) -> Verdict(allow|veto, reason)` against **Johnny's values only** (prompt, FC-3 — no un-loosenable denylist baked in; harm-prevention is the Core mechanisms, not a floor in here); `conscience` role (local) + `config/prompts/conscience.md`; pure `parse_verdict`; fills the cycle CHECK stub (FC-7/FC-9) → `/fastapi-engineer` [TC-6a.2, TC-6a.8]
- [ ] `TASK-6a.4a` `EffectorDispatch` pipeline on the FC-5 `_dispatch_action` point: `propose → vet (Conscience) → (allow) run via registry → write action_log → emit outcome; (veto) write action_log(veto) + emit, never run`. One audited path, no bypass to `tool.run` → `/fastapi-engineer` [TC-6a.3, TC-6a.4]
- [ ] `TASK-6a.4b` Wire the dispatch into the cycle: Conscience fills CHECK, the Effector run fills ACT (FC-7, in place); Deliberation may propose a `(tool,args)` action (the inert tool this phase); one action per tick (P3 bound) → `/fastapi-engineer` [TC-6a.3, TC-6a.9]
- [ ] `TASK-6a.5` `core/audit.py` — append-only `AuditWriter` for `action_log`, import-isolated (FC-1, never imports brain/); route all action_log writes through it → `/fastapi-engineer` [TC-6a.4, TC-6a.7]
- [ ] `TASK-6a.6` Wire `BudgetGovernor` into `LLMRouter.complete()` as a hard pre-call gate (skip cloud step / degrade to local when `over_budget`; log `budget_skip`); revert `deliberation` to cloud-first in `llm_routes.toml` (P3 carry-over resolved) → `/fastapi-engineer` [TC-6a.5]
- [ ] `TASK-6a.7` `/no_think` for the reasoning model on schema roles in `OllamaProvider` (qwen emits clean JSON, no preamble); re-point the sleep `@live` guards (consolidation/self_model/metacognition) to also cover the qwen path now it's reliable (P4 carry-over) → `/fastapi-engineer` [TC-6a.6]
- [ ] `TASK-6a.8` No-secrets-on-the-bus redaction guard on the `workspace_event` + `action_log` write path (deny/redact token/key/secret patterns) — `/audit` can't leak a secret (P5a carry-over) → `/fastapi-engineer` [TC-6a.7]
- [ ] `TASK-6a.9` ⫘ Tests: Conscience allow/veto (values-driven), dispatch vet→run→audit + vet→veto→no-run, action_log shape + append-only, budget hard-gate skips cloud when exhausted (the un-disableable harm bound, independent of the verdict), no-secret-leak redaction → `/qa-test-engineer` [TC-6a.1..6a.5, 6a.7]
- [ ] `TASK-6a.10` ⫘ Contract + `@live`: `parse_verdict` (conscience role) captured-envelope projection; `/no_think` `@live` leg proving qwen returns clean JSON on schema roles (closes the P4 item) → `/qa-test-engineer` [TC-6a.6, TC-6a.8]
- [ ] `TASK-6a.11` ⫘ Security review: every goal→tool path passes through the vet+audit point (no bypass); the harm bound is **mechanism, not Conscience** — verify a permissive/empty Conscience still can't overspend (budget gate holds) or erase its trail; `core/audit.py` is append-only + import-isolated (Mind can't rewrite/delete `action_log`, FC-1); the budget gate genuinely blocks cloud spend when exhausted; the no-secrets guard holds on both write paths; confirm no values-floor leaked into `core/` (FC-9) → `/security-reviewer` [TC-6a.5, TC-6a.7]

## Notes
- **No world-touching tool ships in 6a** — only the inert `noop` tool, to prove the vet→run→audit pipeline deterministically before 6b adds web/code/etc. Resist building a real tool here; the substrate must be reviewable in isolation.
- The **kill switch** (`ctl.sh stop`) already exists (out-of-band hard stop, SPEC §9.3) — 6a doesn't rebuild it. The **integrity gate** (propose→sandbox→approve for self-*code* edits) is **Phase 9**, NOT here — 6a's audit + Conscience are about *tool actions*, not self-modification.
- After 6a, the P3/P4/P5a carried advisories in `plan/TODO.md` are RESOLVED — strike them from the cross-cutting list as each task lands.
