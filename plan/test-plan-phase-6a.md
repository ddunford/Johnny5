# Test Plan: Phase 6a — Safe-action substrate

## Prerequisites
- Phases 0–5 complete; stack up; `inference.lan` reachable (for the `conscience` role + the `/no_think` `@live` leg).
- DB-backed → in-network (`./ctl.sh test`), single runner (lessons.md). Deterministic via frozen clock + stub routers; `@live` legs hit real models.

## Test Cases

### TC-6a.1: Tool registry + typed contract
**Steps:** Register the inert `noop` tool; look it up; run it with valid + invalid args.
**Expected:** Registry resolves by name; valid args → `ToolResult`; args failing the Pydantic schema → a typed validation error (never an untyped crash); a retired tool is no longer resolvable. The tool declares its `danger` class.
**Status:** ⬜

### TC-6a.2: Conscience vets — allow + veto
**Steps:** Feed the Conscience a benign `(tool,args)` and a clearly-harmful one (against a hard invariant); stub the `conscience` router.
**Expected:** Benign → `allow`; harmful → `veto` with a reason. The hard-invariant veto fires even if the values-prompt is permissive (the default denylist is a floor the Conscience can tighten, not loosen below). `parse_verdict` projects the model output cleanly; empty/garbage fails loudly.
**Status:** ⬜

### TC-6a.3: The dispatch path vets BEFORE running
**Steps:** Propose an allowed action and a vetoed action through `EffectorDispatch`; spy on the tool's `run`.
**Expected:** Allowed → Conscience consulted, THEN tool.run executed, outcome emitted. Vetoed → tool.run is **never called**, the veto + reason are recorded, an outcome reflecting the block is emitted. There is no path to tool.run that skips the vet.
**Status:** ⬜

### TC-6a.4: action_log audit trail (append-only, via the Core writer)
**Steps:** Run an allowed + a vetoed action; query `action_log`; attempt a Mind-side delete.
**Expected:** One row per dispatched/vetoed action — tool, args, result (or null on veto), `conscience_verdict`, `veto_reason`, goal_id, success. Written via `core/audit.py`. The Mind has no delete/update path to `action_log` (append-only — grep the Mind for any UPDATE/DELETE on it: none). Surfaced on `/api/v1/audit`.
**Status:** ⬜

### TC-6a.5: BudgetGovernor hard pre-call gate (the P3 carry-over)
**Steps:** With `llm_call_log` seeded so today's spend ≥ `groq_daily_budget_usd`, route a `deliberation`/`consolidation` (cloud-first) call through `LLMRouter.complete()`.
**Expected:** The cloud step is **skipped** (not called) — a `budget_skip` is logged — and the router falls to the local step (or raises `LLMUnavailableError` if none). Under budget → cloud proceeds normally. The clock is injectable so the UTC-day reset is deterministic. `deliberation` is cloud-first in `llm_routes.toml` again (safe now the gate exists).
**Status:** ⬜

### TC-6a.6: `/no_think` makes the qwen fallback reliable for structured output (the P4 carry-over)
**Steps:** `@live` — route a `consolidation`/`self_model`/`metacognition` schema call at the **reasoning model** (qwen) with `/no_think` enabled.
**Expected:** `finish_reason != 'length'`, clean parseable JSON (no reasoning preamble eating the budget). This is the deterministic-empty path the P4 token-budget guards couldn't make reliable — `/no_think` closes it. (Deterministic stub tests can't catch it — needs the live leg.)
**Status:** ⬜

### TC-6a.7: No secret reaches the bus or the audit log
**Steps:** Dispatch an action whose args/result contain a token/key/password-shaped string; broadcast a `workspace_event` with one.
**Expected:** The redaction/deny guard strips/blocks it before persistence — `/api/v1/audit` and the `workspace_event` log show a redaction marker, never the secret. No known secret value (the `.env` Groq key / WS token / DB password) ever appears in `action_log` or `workspace_event`.
**Status:** ⬜

### TC-6a.8: Conscience contract test
**Steps:** Feed `parse_verdict` a captured `conscience` model envelope (allow + veto).
**Expected:** Projects to the typed `Verdict`; empty/garbage fails loudly; no reasoning leakage into the stored verdict/reason.
**Status:** ⬜

### TC-6a.9: No regression — Phases 2–5 still green
**Steps:** Full suite 3× in-network (single runner). The CHECK/ACT fill + the router budget-gate touch the live loop.
**Expected:** All prior cognition/drives/sleep/api tests still pass; the CHECK stage now invokes the Conscience (with the inert tool, benign) without perturbing the heartbeat; budget-gate is inert when under budget. 3× deterministic.
**Status:** ⬜
