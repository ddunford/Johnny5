# QA design — Phase 6b (the tool belt)

> Owner: **qa**. Covers TASK-6b.11 (#13: deterministic per-tool suite + curiosity-loop E2E) and
> TASK-6b.12 (#14: contract + `@live` + the AuditPanel browser E2E + fresh-load smoke).
> This is the executable design written *while impl lands* — it pins exactly what each TC
> asserts, the deterministic doubles, the captured fixtures, and the `@live`/browser legs, so
> turning it into real test files once unblocked is mechanical (and binds to the REAL signatures,
> not guesses — per the code-accuracy house rule).

## Status & blockers (read first)

| My task | TCs | Blocked by | Can start when |
|---|---|---|---|
| #13 TASK-6b.11 | 6b.1–6b.8 (+SSRF/sandbox functional) | #1 (ctl.sh guard), #12 (wiring) | the tool modules + `WebReadConsolidator` + `Scheduler` + extended `Deliberation` exist and are wired |
| #14 TASK-6b.12 | 6b.1, 6b.7, 6b.13, 6b.14 | #1, #12, #16 (frontend 6b.14) | a tool has run (durable rows exist) **and** the lead's durable-trail panel + `auditActionsApi.ts` adapter exist |

**I do not execute anything against the stack until unblocked.** When I do run the suite I will
`docker ps | grep -E 'api-run|test'` first and ping the lead for the single-runner DB window
(lessons.md: 6a collision). A detached `./ctl.sh test` looks idle in chat but is live.

## Test discipline (updated — concurrency guard is LIVE as of 78cdd5c)

- **The ctl.sh test concurrency guard (TASK-6b.0) is live + verified.** Each `./ctl.sh test` run
  claims an isolated slot (1..15) → its own DB (`johnny5_test_be_<slot>`) + Redis db + container, via
  `flock`. **Parallel runs are now safe** — the 6a shared-`johnny5_test` collision class is gone, so
  the strict "one runner at a time / ping before every run" rule is relaxed. Still give the lead a
  **heads-up before a long full-suite run** (so they know what's happening), and `docker ps` is still
  a good sanity check, but a collision can no longer corrupt another run.
- **`@live` legs still hit SHARED external infra** (real SearXNG `inference.lan:8889`, the real
  sandbox container) — those are NOT slot-isolated, so **coordinate `@live` runs** with the lead.
- **SearXNG JSON is enabled**; working engines are **google, bing, brave** (duckduckgo returns 0 from
  that box). The stub SearXNG canned body must be captured from a real query against these engines
  (verify-the-live-contract), and the `@live` web_search/news tests must use a query that returns hits
  from google/bing/brave — never assert against duckduckgo results.
- DB/Redis-backed tests run **in-network** (`./ctl.sh test`), never host pytest (compose hostnames
  `postgres`/`redis` don't resolve on the host → false connection errors).
- Determinism proven by running the new suite **3×** green (one green run isn't proof — loop-scope /
  contention flakes hide in a lucky pass).
- I own `tests/` and `frontend/tests/`. I do **not** commit (lead is sole committer) and never run
  destructive git on the shared tree. Product bugs → reported to the lead, not fixed by me.

## Reconcile outcome (2026-05-27) — dedupe vs the backend's own unit tests

The backend teammate committed unit tests alongside each tool. To avoid shipping two
overlapping suites per tool ("search before create"), QA reconciled:

| TC | Owning test file (kept) | My duplicate (removed) | Net QA contribution |
|---|---|---|---|
| 6b.2 | `tests/effectors/test_web_fetch.py` | `tests/tools/test_web_fetch.py` | ported the missing **text/plain** extraction test into the kept file |
| 6b.9 | `tests/effectors/test_safe_http.py` | (same file above) | none unique — backend covers the full SSRF gate, same technique |
| 6b.5 | `tests/effectors/test_notes.py` | `tests/tools/test_note.py` | dispatch-vet→`action_log` acceptance moved to the TC-6b.11 wiring suite |
| 6b.8 | `tests/effectors/test_memory_tools.py` | `tests/tools/test_memory_tools.py` | none unique (backend file is a superset — has the empty-search case) |
| 6b.6 | `tests/effectors/test_scheduler.py` | (never written) | run-loop `fire_due`-between-ticks check → TC-6b.11 wiring suite |

So `tests/tools/` now holds **only net-new tool tests** (web_search / news / code_exec) once
those land; the per-tool tests the backend already wrote are the source of truth. The acceptance
gaps the unit suites legitimately *don't* cover (a real tool through the real dispatch→audit; the
cycle actually calling `Scheduler.fire_due`) live in the **TC-6b.11 wiring suite** (mine), once #12
lands — one representative assertion each, not per-tool duplication.

## Naming

Feature-named, no phase numbers in filenames (code-accuracy rule):

| File | Covers |
|---|---|
| `tests/tools/test_web_search.py` | TC-6b.1 |
| `tests/tools/test_web_fetch.py` | TC-6b.2, TC-6b.9 (functional SSRF) |
| `tests/tools/test_news.py` | TC-6b.3 |
| `tests/tools/test_note.py` | TC-6b.5 |
| `tests/tools/test_schedule_wakeup.py` | TC-6b.6 |
| `tests/tools/test_code_exec.py` | TC-6b.7, TC-6b.10 (functional sandbox) |
| `tests/tools/test_memory_tools.py` | TC-6b.8 |
| `tests/tools/test_tool_belt_registry.py` | TC-6b.11 (all tools registered, dispatch-only) |
| `tests/cognition/test_curiosity_loop.py` | TC-6b.4 (the headline E2E) |
| `tests/tools/test_web_search_live.py` (`@live`) | TC-6b.1 live SearXNG |
| `tests/tools/test_code_exec_live.py` (`@live`) | TC-6b.7 live sandbox |
| `tests/helpers/tool_doubles.py` | shared stub SearXNG / stub summariser / stub origin |
| `frontend/tests/e2e/audit-panel.spec.ts` | TC-6b.13 (browser E2E, empty + populated) |
| `frontend/tests/e2e/fresh-load-smoke.spec.ts` (extend) | TC-6b.14 part 2 (durable trail empty cold-load) |
| `frontend/src/services/adapters.contract.test.ts` (extend) | TC-6b.14 part 1 (adapter contract) |
| `tests/fixtures/wire/audit_actions.json` + `.empty.json` | TC-6b.14 captured wire fixtures |

> `tests/tools/` is a new package — mirror `tests/effectors/`: add `conftest.py` for the DB-backed
> tools (note/memory/schedule) reusing `helpers.db` (`install_fresh_global_engine` +
> `truncate_tables`) and a real `Workspace` on flushed Redis. Pure tools (web_search/web_fetch/news
> with a stubbed client, code_exec dry) need no DB fixture.

---

## Shared deterministic doubles (`tests/helpers/tool_doubles.py`)

These are the determinism backbone. Exact wiring (constructor injection vs. settings) gets pinned
against the REAL tool signatures once they land — design intent here:

- **Stub SearXNG** — the web/news tools hit SearXNG over HTTP (`inference.lan:8889`). Inject a stub
  at the tool's HTTP boundary. Preferred seam: the tool takes an injectable `httpx.AsyncClient`
  (or a `base_url`/transport) so a test passes an `httpx.MockTransport` returning canned SearXNG
  JSON (the real SearXNG `/search?format=json` envelope — capture one `@live` response first and
  freeze it as the canned body, per "verify the live contract before writing clients").
  - Happy: returns N ranked `{title,url,content/snippet}` results.
  - Down: transport raises `httpx.ConnectError` → tool returns a graceful `ToolResult(success=False, …)`,
    **not** an exception that crashes the cycle.
- **Stub summariser** — `WebReadConsolidator` reuses the Phase-4 consolidation summariser (an LLM
  role via the router). Drive it with `helpers.llm.CannedProvider` returning a fixed summary +
  fact triple JSON, wired through `make_router({"consolidation"/<role>: [...]}, …)`. No network.
- **Stub origin server** (web_fetch) — an `httpx.MockTransport` (or a tiny in-process ASGI app on a
  127.0.0.1 ephemeral port for the redirect-hop tests) serving: a readable HTML doc (boilerplate +
  article), an over-size body, a slow/timeout endpoint, and a 302 → internal-IP redirect.
- **Frozen clock** — reuse `helpers.clock.FrozenClock` + `helpers.cycle.datetime_from` so wakeup
  `fire_at` due-checks and provenance timestamps are exact.

---

## Per-TC design

### TC-6b.1 — `web_search` (`tests/tools/test_web_search.py`)
- **happy**: stub SearXNG → assert results are the typed shape (`title`/`url`/`snippet`), ranked
  order preserved, `ToolResult.success is True`.
- **arg-validation**: empty/blank `query` → `validate_args` raises `ValidationError` (the dispatch's
  typed-before-vet guarantee; mirror `test_tool_registry.py`). Confirm `args_schema` is the tool's
  own, `extra="forbid"`.
- **graceful failure**: stub transport raises `ConnectError` → `ToolResult(success=False)` with an
  error summary; assert no exception propagates (the cycle survives a dead SearXNG).
- **vet path**: dispatched through a real `EffectorDispatch` with a `CannedProvider` allow verdict →
  one `action_log` row, `danger == network` stamped (reuse the `test_action_audit.py` DB pattern).

### TC-6b.2 — `web_fetch` (`tests/tools/test_web_fetch.py`)
- **happy**: stub origin serves article HTML → returns readable text with boilerplate stripped.
- **size cap**: over-size body → truncated at the limit (assert length ≤ cap, success True/flagged).
- **time cap**: slow endpoint → aborted cleanly as a tool error, not a hang (use a tiny timeout in test).
- **scheme allowlist**: `file://`, `gopher://`, `ftp://` → rejected before any fetch (typed/arg error
  or `ToolResult(success=False)`; assert the transport was never called).
- These are the *functional* half of SSRF (TC-6b.9); the adversarial review is the lead's TASK-6b.13.

### TC-6b.3 — `news` (`tests/tools/test_news.py`)
- stub SearXNG news → recent items in the typed shape; topic filter narrows; recency ordering.
- vet path: one `action_log` row, `danger == network`.

### TC-6b.4 — the curiosity loop (HEADLINE, `tests/cognition/test_curiosity_loop.py`)
The full **drive → goal → web tool → read → remember → ease**, deterministic. Build on
`helpers.cycle.build_cycle` but wire the REAL stages that matter and stub the world:
1. Frozen clock; stub SearXNG (canned news/search results); stub summariser (canned summary + fact).
2. Seed Curiosity over threshold (via `StubDrives` readings or the real DriveEngine seeded high).
3. Run ticks → assert: an arbiter goal is raised from the Curiosity urge → **Deliberation proposes a
   `(tool=news|web_search, args=…)`** external action (TASK-6b.9) → Conscience **allows** (canned
   verdict) → the tool runs via the vetted dispatch → **`WebReadConsolidator` writes an `episode`
   + a `semantic_fact` with `provenance == url`** → a satisfaction event eases Curiosity (assert the
   drive value drops below threshold / a `drive.update` toward setpoint).
4. **Later recall surfaces the fact**: query memory (the real recall path) → the consolidated fact is
   returned, grounding a subsequent thought.
- Assert determinism by running 3×; assert the action cadence (one external action per tick, budget
  gate respected) so an idle curious Johnny doesn't hammer the stub every tick (ties to TC-6b.12).
- DB-backed (episode/semantic_fact/action_log) → in-network, `tests/cognition/conftest.py`-style
  fresh slate. This is the single most important test in the phase — it proves the phase thesis.

### TC-6b.5 — `note` (`tests/tools/test_note.py`)
- write a note via the tool → row in `note` (title/body/tags/ts); read back via the tool.
- vetted (allow verdict) + `action_log` row; `danger == safe`.

### TC-6b.6 — `schedule_wakeup` (`tests/tools/test_schedule_wakeup.py`)
- schedule at `fire_at` (frozen clock) → `scheduled_wakeup` row, status pending.
- advance the clock past `fire_at`, run the loop's due-check → a self-percept is injected (assert a
  percept/workspace item appears) and status → `fired`.
- **no double-fire**: advance again → the already-fired wakeup is not re-injected.

### TC-6b.7 — `code_exec` (`tests/tools/test_code_exec.py`, deterministic)
- trivial snippet → captured stdout/result (dry/stub dispatch — the sandbox container itself is the
  `@live` leg in 6b.12). Snippet that raises → captured error in `ToolResult`, not a cycle crash.
  Snippet exceeding timeout/memory → killed + reported (simulate the kill at the dispatch seam).
- Functional half of TC-6b.10; adversarial escape review is the lead's TASK-6b.13.

### TC-6b.8 — `memory_search` / `memory_write` (`tests/tools/test_memory_tools.py`)
- `memory_write` persists (episodic + semantic); `memory_search` returns it ranked.
- both vetted + audited; `danger == safe`.

### TC-6b.9 / TC-6b.10 — functional security coverage (in the web_fetch / code_exec files)
I write the **deterministic/functional** assertions (scheme allowlist, post-DNS deny basic cases,
redirect-hop re-check via the stub origin 302→internal; sandbox: error capture, timeout kill, the
no-host-mount assertion at the dispatch contract). The **adversarial** review (real `169.254.169.254`,
real private ranges, real container escape attempts, prompt-injection-from-fetched-content into the
Conscience vet, novel-secret redaction on fetched content) is the lead's **TASK-6b.13** — I
coordinate so we don't double-cover or leave a gap. See "Security split" below.

### TC-6b.11 — tools wired + read surface (`tests/tools/test_tool_belt_registry.py`)
- boot the runtime registry (the production wiring from TASK-6b.10) → assert every tool name is
  registered with its declared `danger` class.
- the dispatch path is the only run path (source guard, like `test_action_audit.py`'s no-mutation
  guard: nothing runs a tool except via `EffectorDispatch`).
- `GET /api/v1/audit/actions` and the `note` read endpoint return (DB-backed, via `build_api_app`
  TestClient — this is an **API-level** read assertion, explicitly allowed; the *UI* render is
  TC-6b.13). No regression to Phases 2–5 (the existing suites stay green).

### TC-6b.12 — no regression + cost-bound
- Full suite 3× in-network (coordinate the DB window). Phases 2–6a green.
- idle-curious cadence + budget gate: assert the curiosity loop respects one-action-per-tick and the
  BudgetGovernor gate (no per-tick hammering) — driven from TC-6b.4's deterministic harness with a
  `MemoryCallLogger` / budget gate stub asserting skips.

---

## Frontend — the durable-trail AuditPanel (TC-6b.13 + TC-6b.14)

> **Critical distinction**: `/api/v1/audit` → `{events:[…]}` is the **bus feed** (already shipped in
> 5b; `auditApi.ts` + the current `AuditPanel`). My TCs target the **durable `action_log` trail**
> `/api/v1/audit/actions` → `{actions:[…]}` (the Core-written, FC-1 record). The lead's **TASK-6b.14**
> wires a panel/section to this and adds the `auditActionsApi.ts` adapter. My tests bind to *that*.

### The wire shape (from `johnny/api/v1/schemas.py::ActionAuditResponse`, verified)
```jsonc
{ "actions": [ {
  "id": 1, "ts": "2026-05-27T…+00:00", "tool": "note",
  "args": { … }, "result": { … } | null,
  "conscience_verdict": "allow" | "veto",
  "veto_reason": null | "…", "goal_id": 1 | null, "success": true
} ] }
```
Empty (the default until a tool runs): `{ "actions": [] }`.
Args/result are redaction-guarded on the way out (`redact_payload`) → a secret renders as the
`[REDACTED]` marker, never the raw value. **This is the assertion that proves no-secrets-on-the-UI.**

> **Ownership (lead ruling 2026-05-27):** the lead owns TASK-6b.14 build + the **frontend adapter
> unit contract test** (`adaptAuditActions`). I own the **browser E2E** (TC-6b.13), the **fresh-load
> smoke** (TC-6b.14 part 2), the **backend tool-result projection contracts**, and the `@live` legs.
> The committed DOM/adapter contract is pinned below — my specs (`audit-panel.spec.ts`, the
> `fresh-load-smoke.spec.ts` addition) are written against it and live now.

> **Committed DOM contract (lead, TASK-6b.14):** a NEW "Action trail" section ABOVE the existing
> "Live bus" feed on the same `/audit` route (two labelled sections, no toggle). `data-testid`s:
> `audit-actions` (section), `audit-actions-empty` (empty, copy EXACTLY
> `"No actions yet — nothing has run through the dispatch."`), `audit-action-row` per row with
> `audit-action-tool` / `audit-action-verdict` (text `allow`|`veto`) / `audit-action-reason`
> (veto-only); veto rows carry class `audit-action--veto`. A `<select>` labelled "Filter by verdict"
> (all/allow/veto) drives `?verdict=`. args/result are text-only → a redacted value shows the literal
> `[REDACTED]`. Adapter: `auditActionsApi.ts` → `adaptAuditActions(envelope): ActionAudit[]` +
> `fetchAuditActions({limit?,verdict?})`; hook `useAuditActions` in `@/hooks/reads`; the `ActionAudit`
> TS type mirrors `schemas.py:ActionAudit` verbatim.

### TC-6b.14 part 1 — adapter contract test (LEAD-OWNED; extends `adapters.contract.test.ts`)
- Capture the fixtures (NOT hand-authored — house rule):
  - `audit_actions.empty.json` ← curl a fresh stack: `GET /api/v1/audit/actions` → `{"actions":[]}`.
  - `audit_actions.json` ← after a `note` write (and a vetoed action, to cover `result:null` +
    `veto_reason`) has run through dispatch, curl the same endpoint and freeze the body.
    To force a veto deterministically for the capture, dispatch a `note` with a canned veto verdict
    (coordinate with the lead — or capture from the curiosity-loop run which produces an allow row,
    and add a separate vetoed capture). The fixture MUST contain at least one allow row (with
    `result`) and one veto row (`result:null`, `veto_reason` set) to exercise both branches.
  - **Include a `[REDACTED]` marker in a captured arg/result** so the contract pins redaction-on-read
    (dispatch a tool whose args carry a known secret shape, then capture — coordinate with lead).
- Feed `adaptAuditActions` (lead's adapter) the LITERAL fixture (populated + empty); assert the full
  projection against hand-written EXPECTED literals (NOT derived from the fixture — circular).
- **wishlist guard**: mutate the fixture to simulate a server rename (`conscience_verdict` → `verdict`)
  → the same field-for-field assertion must throw. Proves the contract pins the REAL wire.

### TC-6b.13 — AuditPanel browser E2E (`frontend/tests/e2e/audit-panel.spec.ts`)
Playwright against the REAL running stack (NOT route mocks), reusing `support/app.ts`
(`attach`, `collectPageErrors`, `nullishCrashes`, `gotoApp`):
- **(a) empty** (fresh stack, no tool run): navigate to the durable-trail view → assert the
  empty-state copy renders (e.g. "No actions yet." — **copy TBD by lead's 6b.14; coordinate**),
  `collectPageErrors` empty, `nullishCrashes` empty. This is the P5b crash-on-first-load class:
  the panel's first-ever load has zero rows — it must not throw `Cannot read properties of undefined`.
- **(b) populated** (after a tool dispatched): reload → assert durable rows render (tool, verdict,
  ts, success); a **veto row shows its reason**; an arg/result containing a secret shows `[REDACTED]`
  and **never the raw value** (assert the page does NOT contain the secret string). Zero console errors.
- Browser-rendered assertions only (not an API status check). `test.skip(!TOKEN)` like the others.

### TC-6b.14 part 2 — fresh-load smoke (extend `fresh-load-smoke.spec.ts`)
The existing smoke already cold-loads `/audit` and asserts "Nothing on the bus yet." (the bus feed).
Add the **durable-trail** empty assertion to the same cold walk: land on the durable-trail view with
an empty `action_log` → its empty-state renders, zero console errors, zero nullish crashes. Empty is
the DEFAULT until a tool runs — cover it explicitly, not just the populated path.

---

## `@live` legs (TC-6b.12)

- **`tests/tools/test_web_search_live.py`** (`@pytest.mark.live`): real round-trip to
  `inference.lan:8889` → assert the real SearXNG JSON envelope matches the typed shape the stub
  models (this is what keeps the stub honest — verify-the-live-contract). Skipped on host; runs
  in-network only. Guard wall-clock per the live-token lesson.
- **`tests/tools/test_code_exec_live.py`** (`@pytest.mark.live`): real exec in the actual hardened
  sandbox container (TASK-6b.6a) → trivial snippet returns stdout; confirms it runs isolated.
- **Frontend `@live`**: the browser E2E + fresh-load smoke ARE the live legs (real stack, real
  responses). No additional `@live` vitest.

---

## Security split with the lead (TC-6b.9 / TC-6b.10 → TASK-6b.13)

| Concern | qa (functional, deterministic) | lead (adversarial review, 6b.13) |
|---|---|---|
| web_fetch SSRF | scheme allowlist; stub 302→internal blocked at hop; basic private-IP deny | real `127.0.0.1`/`localhost`/`10.x`/`192.168.x`/`172.16.x`/`169.254.169.254`/`inference.lan`; DNS-rebind; per-redirect re-check exhaustively |
| code_exec sandbox | error capture; timeout kill; no-host-mount at the dispatch contract | real container: `/etc/passwd`/repo read blocked, socket blocked, fork-bomb/mem cap, non-root, timeout kill |
| prompt-injection | n/a | fetched page content in the Conscience vet prompt can't flip the verdict |
| redaction on fetched content | the `[REDACTED]` assertion in the audit UI + contract | novel-secret shapes lifted from a page (carried 6a advisory) |

I will hand the lead my functional results + the stub-origin redirect fixture so the adversarial
review reuses the same seams.

## Open questions for the lead (coordination)
1. **Durable-trail panel DOM contract** (6b.14): empty-state copy string, row roles/test-ids, how
   `[REDACTED]` renders — so my E2E selectors + smoke copy match exactly (avoids guess-churn).
2. **Adapter name/shape**: confirm `auditActionsApi.ts` exports `adaptAuditActions(envelope) →
   ActionAudit[]` and a `fetchAuditActions(query)`; confirm the view type field names so my contract
   EXPECTED literals are right.
3. **Tool injection seams**: do web/news tools take an injectable `httpx.AsyncClient`/transport, and
   does `WebReadConsolidator` take an injectable summariser/router? (Determines stub wiring.)
4. **Fixture capture**: agree a deterministic way to produce an allow row + a veto row + a redacted
   arg for the `audit_actions.json` capture (I curl; we don't hand-author).
