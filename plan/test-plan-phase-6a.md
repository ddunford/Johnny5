# Test Plan: Phase 6a — Safe-action substrate

## Prerequisites
- Phases 0–5 complete; stack up; `inference.lan` reachable (for the `conscience` role + the `/no_think` `@live` leg).
- DB-backed → in-network (`./ctl.sh test`), single runner (lessons.md). Deterministic via frozen clock + stub routers; `@live` legs hit real models.

## Test Cases

### TC-6a.1: Tool registry + typed contract
**Steps:** Register the inert `noop` tool; look it up; run it with valid + invalid args.
**Expected:** Registry resolves by name; valid args → `ToolResult`; args failing the Pydantic schema → a typed validation error (never an untyped crash); a retired tool is no longer resolvable. The tool declares its `danger` class.
**Status:** ✅ `tests/effectors/test_tool_registry.py` — register/resolve/retire, valid→`ToolResult`, bad args ({}, wrong type, extra field)→`ValidationError`, retired unresolvable, `danger` declared. In-network green.

### TC-6a.2: Conscience vets — allow + veto (values-driven, fully editable)
**Steps:** Feed the Conscience a benign `(tool,args)` and one its values-prompt should refuse; stub the `conscience` router. Then swap in a deliberately permissive values-prompt and re-run the refused action.
**Expected:** Benign → `allow`; values-violating → `veto` with a reason. With the permissive prompt the same action now → `allow` — the Conscience is **pure editable values with no un-loosenable floor** (FC-9); there is no hard denylist baked into the Conscience. (That a permissive Conscience still can't cause host-harm is proven by the Core mechanism layer — the budget gate in TC-6a.5, and 6b's sandbox/SSRF — NOT by a floor in here.) `parse_verdict` projects the model output cleanly; empty/garbage fails loudly.
**Status:** ✅ `tests/agents/test_conscience.py` — benign→allow, values-violating→veto+reason; **permissive prompt flips the SAME action veto→allow** (no un-loosenable floor, FC-9), with the loaded git-backed prompt proven reaching the model; fail-closed → veto when the Conscience model is unavailable. Stubbed router, green.

### TC-6a.3: The dispatch path vets BEFORE running
**Steps:** Propose an allowed action and a vetoed action through `EffectorDispatch`; spy on the tool's `run`.
**Expected:** Allowed → Conscience consulted, THEN tool.run executed, outcome emitted. Vetoed → tool.run is **never called**, the veto + reason are recorded, an outcome reflecting the block is emitted. There is no path to tool.run that skips the vet.
**Status:** ✅ `tests/effectors/test_effector_dispatch.py` — allowed→Conscience consulted→`tool.run` ran→`action.dispatched`; vetoed→**spy asserts `tool.run` 0 calls**→`action.vetoed`+reason; tool hazard class stamped before vet; unknown tool/bad args fail typed BEFORE the vet; staged `vet`→`commit` keeps the same no-bypass gate. Green.

### TC-6a.4: action_log audit trail (append-only, via the Core writer)
**Steps:** Run an allowed + a vetoed action; query `action_log`; attempt a Mind-side delete.
**Expected:** One row per dispatched/vetoed action — tool, args, result (or null on veto), `conscience_verdict`, `veto_reason`, goal_id, success. Written via `core/audit.py`. The Mind has no delete/update path to `action_log` (append-only — grep the Mind for any UPDATE/DELETE on it: none). Surfaced on `/api/v1/audit`.
**Status:** ✅ `tests/effectors/test_action_audit.py` — allow row (full shape: tool/args/result/verdict/goal_id/success) + veto row (result null, veto_reason set) written via `core/audit.py`; one row per action; **read-after-write through real `GET /api/v1/audit/actions`** (verdict filter incl.); append-only source guard (no UPDATE/DELETE on `action_log` anywhere in brain/johnny/core) + read model exposes no mutating method; surfaced as `action.dispatched`/`action.vetoed` on the bus. DB-backed, green.

### TC-6a.5: BudgetGovernor hard pre-call gate (the P3 carry-over)
**Steps:** With `llm_call_log` seeded so today's spend ≥ `groq_daily_budget_usd`, route a `deliberation`/`consolidation` (cloud-first) call through `LLMRouter.complete()`.
**Expected:** The cloud step is **skipped** (not called) — a `budget_skip` is logged — and the router falls to the local step (or raises `LLMUnavailableError` if none). Under budget → cloud proceeds normally. The clock is injectable so the UTC-day reset is deterministic. `deliberation` is cloud-first in `llm_routes.toml` again (safe now the gate exists).
**Status:** ✅ `tests/llm/test_budget_gate.py` — seed `llm_call_log` ≥ budget today → cloud step skipped (`groq.calls==0`, `budget_skip` logged) → degrades to local; under budget → cloud proceeds; cloud-only chain over budget → `LLMUnavailableError`; **injected clock proves UTC-midnight reset** (yesterday's huge spend excluded); free local step never gated. DB-backed, green.

### TC-6a.6: `/no_think` makes the qwen fallback reliable for structured output (the P4 carry-over)
**Steps:** `@live` — route a `consolidation`/`self_model`/`metacognition` schema call at the **reasoning model** (qwen) with `/no_think` enabled.
**Expected:** `finish_reason != 'length'`, clean parseable JSON (no reasoning preamble eating the budget). This is the deterministic-empty path the P4 token-budget guards couldn't make reliable — `/no_think` closes it. (Deterministic stub tests can't catch it — needs the live leg.)
**Status:** ✅ `tests/llm/test_no_think_live.py` (backend-owned) — confirmed via `./ctl.sh test -m live --run-live`: **2 passed in 68s**, `finish_reason != 'length'`, clean parseable JSON on both a flat verdict and a nested consolidation-shaped schema against the real qwen reasoning model. (QA ran it; did not edit the file.)

### TC-6a.7: No secret reaches the bus or the audit log
**Steps:** Dispatch an action whose args/result contain a token/key/password-shaped string; broadcast a `workspace_event` with one.
**Expected:** The redaction/deny guard strips/blocks it before persistence — `/api/v1/audit` and the `workspace_event` log show a redaction marker, never the secret. No known secret value (the `.env` Groq key / WS token / DB password) ever appears in `action_log` or `workspace_event`.
**Status:** ✅ `tests/effectors/test_redaction.py` — pure `redact_payload` (sensitive keys, credential-shaped strings incl. Groq/OpenAI/AWS/JWT/Bearer, nested recursion, no-op on benign); both real write paths (`AuditWriter.record`→`action_log`, `Workspace.broadcast`→`workspace_event`) redact before persistence; **real configured `.env` secrets never appear** in either sink; `[REDACTED]` served (never the secret) through real `GET /api/v1/audit/actions`. DB-backed, green.

### TC-6a.8: Conscience contract test
**Steps:** Feed `parse_verdict` a captured `conscience` model envelope (allow + veto).
**Expected:** Projects to the typed `Verdict`; empty/garbage fails loudly; no reasoning leakage into the stored verdict/reason.
**Status:** ✅ `tests/agents/test_conscience_contract.py` — fed REAL captured gemma4 conscience envelopes (`tests/fixtures/llm/conscience_gemma4_{allow,veto}.json`, captured live from inference.lan; manifest-documented). Two-layer projection (`parse_chat_completion` content-first → `parse_verdict` → typed `Verdict`): allow→allow, veto→veto with the model's real first-person reason; the 1213/1472-char reasoning chain never leaks into the stored verdict/reason; empty/non-JSON/invalid-literal/missing-field all raise `ValidationError` (fail loudly); stray JSON fields dropped (extra="ignore"). Pure, host-green (9 passed).

### TC-6a.9: No regression — Phases 2–5 still green
**Steps:** Full suite 3× in-network (single runner). The CHECK/ACT fill + the router budget-gate touch the live loop.
**Expected:** All prior cognition/drives/sleep/api tests still pass; the CHECK stage now invokes the Conscience (with the inert tool, benign) without perturbing the heartbeat; budget-gate is inert when under budget. 3× deterministic.
**Status:** ✅ Full suite 3× confirmed-solo (single runner): **325 passed, 14 skipped** each run (425s / 458s / 471s), zero `IntegrityError`, deterministic. Plus `tests/effectors/test_cycle_dispatch.py` — a real `CognitiveCycle` tick runs CHECK (Conscience) + ACT (dispatch→`action_log`) with **no degraded stage** (heartbeat intact); allow runs the tool + surfaces `action.dispatched`, veto records but never runs; repeated ticks append one row each. (The 14 skips are the `@live` legs, deselected without `--run-live`.)
