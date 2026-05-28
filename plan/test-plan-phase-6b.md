# Test Plan: Phase 6b — The tool belt

## Prerequisites
- Phase 6a complete (Tool registry + Conscience + vetted dispatch + audit + budget gate + `/no_think` + no-secrets guard).
- Stack up; SearXNG (`inference.lan:8889`) reachable for `@live`; the sandbox image built. DB-backed → in-network single runner. Deterministic via frozen clock + stub SearXNG + stub summariser; `@live` legs hit real SearXNG + a real sandbox container.

## Test Cases

### TC-6b.1: `web_search` (SearXNG)
**Steps:** Run `web_search` with a query (stub SearXNG for the deterministic test; one `@live` against `inference.lan:8889`).
**Expected:** Returns ranked results (title/url/snippet) in the typed shape; empty query → arg-validation error; SearXNG-down → graceful tool error (not a cycle crash). `@live` confirms the real contract.
**Status:** ✅ `tests/effectors/test_web_search.py` (ranked results, caps, SearXNG-down graceful, empty-results, strict args) + `test_searxng.py` (client + projection pinned to captured envelopes, **incl. `@live` round-trips** `test_live_searxng_*`). Backend-authored, QA-verified green.

### TC-6b.2: `web_fetch` extracts clean text
**Steps:** Fetch an allowed http(s) URL (stub server); assert extraction.
**Expected:** Returns readable text (boilerplate stripped), capped at the size limit; non-http(s) scheme → rejected; over-size/over-time → truncated/aborted cleanly.
**Status:** ✅ `tests/effectors/test_web_fetch.py` (backend-authored unit suite, TASK-6b.3) — 18 green: extraction strips script/style/nav/footer + keeps blocks separate; byte/char caps; timeout/connection/4xx/non-text → graceful `success=False`; non-http(s)/hostless → typed `ValidationError`. QA reconcile: I dropped my overlapping `tests/tools/test_web_fetch.py` (dedup, "search before create") and ported the one genuine gap it had — the **text/plain extraction branch** (`test_run_returns_plain_text_collapsed…`) — into this file.

### TC-6b.3: `news` browsing
**Steps:** Browse news by topic/recency (stub + one `@live`).
**Expected:** Returns recent items in the typed shape; topic filter works; this is the curiosity feed Deliberation pulls.
**Status:** ✅ `tests/effectors/test_news.py` (news category, newest-first dated-before-undated, count cap, SearXNG-down graceful, strict args) + `test_searxng.py` news projection + `@live` news round-trip. Backend-authored, QA-verified green.

### TC-6b.4: The curiosity loop (the headline)
**Steps:** Frozen clock, stub SearXNG + stub summariser. Drive Curiosity over threshold → let the cycle run.
**Expected:** Curiosity urge → arbiter goal → Deliberation proposes a `news`/`web_search` `(tool,args)` → Conscience allows → tool runs → `WebReadConsolidator` writes an **episode + semantic fact with url provenance** → the satisfaction event eases Curiosity. A later recall surfaces the consolidated fact. The full drive→world→memory→ease loop, deterministic.
**Status:** ✅ Covered in three layers, all green in-network: **(qa) `tests/effectors/test_curiosity_loop.py`** — the full REAL-component E2E: idle accrual → real Deliberation proposes a `news` tool action → real vetted+audited dispatch (one `action_log` row) → real `WebReadConsolidator` writes a `web_read` episode + a provenance-linked fact (`fact.source_episode_ids == [episode.id]`) → Curiosity eased below threshold → goal resolved → a later episodic + semantic recall surfaces it; plus a veto path that eases nothing. **(backend) `test_curiosity_loop_wiring.py`** — the cycle orchestrates ease-on-consolidation (not on raw fetch). **(backend) `test_deliberation_external_tools.py`** — the drive→tool planning map. Deterministic (frozen clock + stub SearXNG + `router=None` fallback + deterministic embedder).

### TC-6b.5: `note` (journal)
**Steps:** Write a note via the tool; read it back.
**Expected:** Persists to `note` (title/body/tags/ts); readable; Conscience-vetted + in `action_log`.
**Status:** ✅ `tests/effectors/test_notes.py` (backend-authored, TASK-6b.7) — write→read-back, newest-first ordering, `notes_to_payload` wire shape, strict args. QA reconcile: dropped my overlapping `tests/tools/test_note.py`. The "Conscience-vetted + in `action_log`" acceptance angle (a *real* tool through the real dispatch → durable row) is the dispatch's generic guarantee — covered by 6a's dispatch tests + folded into the TC-6b.11 wiring suite (one real-tool dispatch→`action_log` assertion), not duplicated per tool.

### TC-6b.6: `schedule_wakeup` (self-prompt)
**Steps:** Schedule a wakeup at `fire_at` (frozen clock); advance past it.
**Expected:** `scheduled_wakeup` persisted; when the run loop's due-check passes `fire_at`, a self-percept is injected (Johnny "remembers" to do the thing); status → fired. Past wakeups don't double-fire.
**Status:** ✅ `tests/effectors/test_scheduler.py` (authored by the backend teammate, TASK-6b.8) — 13 tests green in-network: persists pending; doesn't fire before `fire_at`; fires once past it (status→fired) injecting a self-percept carrying the reason; **no double-fire** (atomic claim); injected-clock; tool-schedules-via-Scheduler; strict args. QA reviewed — no duplication added. ⚠️ One integration check deferred to TC-6b.11 (wiring): assert the **cycle actually calls `Scheduler.fire_due` between ticks** (the FC-7 run-loop phase) — the unit test drives `fire_due` directly; the run-loop wiring lands with #12.

### TC-6b.7: `code_exec` runs in the sandbox
**Steps:** Run a trivial Python snippet (stub/dry for the deterministic test; one `@live` against the real sandbox container).
**Expected:** Returns stdout/result; a snippet that errors → captured error, not a cycle crash; a snippet that exceeds the timeout/memory → killed + reported. `@live` confirms it runs in the isolated container.
**Status:** ✅ (deterministic) `tests/effectors/test_code_exec.py` (stdout projection, user-exception graceful, timeout reported, resource-kill reported, sandbox-unavailable graceful, oversize-snippet rejected, strict args) + `test_code_exec_contract.py` (`SandboxVerdict` pinned to the launcher envelope). Backend-authored, QA-verified green. ⏳ The **`@live` real-sandbox-container exec** leg is TC-6b.12 / #14 (gated on the api recreate — coordinated with the lead).

### TC-6b.8: `memory_search` / `memory_write`
**Steps:** Write a memory + search for it via the tools.
**Expected:** Write persists (episodic/semantic); search returns it ranked; both Conscience-vetted + audited.
**Status:** ✅ `tests/effectors/test_memory_tools.py` (backend-authored, TASK-6b.7) — deterministic axis-vector embedder: `memory_write` persists an episode; `memory_search` blends episodic + semantic recall (returns the lived episode AND the consolidated fact, ranked); empty-search returns `[]`; strict args. QA reconcile: dropped my overlapping `tests/tools/test_memory_tools.py` (it was a strict subset — the backend file additionally covers the empty-search case).

### TC-6b.9: SSRF hardening on `web_fetch` (CRITICAL, blocking)
**Steps:** Attempt fetches to: `http://127.0.0.1`, `http://localhost`, a private IP (`10.x`/`192.168.x`/`172.16.x`), link-local (`169.254.169.254` cloud metadata), `inference.lan`, a `file://`/`gopher://` scheme, and a public URL that 302-redirects to one of those.
**Expected:** Every internal/private/metadata target is **blocked** (post-DNS IP check), incl. on the redirect hop; non-http(s) schemes rejected; only genuinely-public http(s) is fetched. The redirect-to-internal case is blocked at the hop, not just the initial URL.
**Status:** 🟡 Functional floor ✅ — `tests/effectors/test_safe_http.py` (backend-authored, TASK-6b.3): `ip_is_blocked` deny-list table (loopback/private/link-local/ULA/metadata/multicast/unparseable blocked; global unicast passes); `validate_target` refuses bad scheme/embedded-creds/internal-IP-literal/hostname→internal/split-record + pins the validated IP + keeps non-default port in the Host header; via stub resolver + `httpx.MockTransport`: a 302→internal IP and a 302→metadata are **blocked at the hop**, happy fetch pins to the validated IP, byte-cap truncation, redirect-loop cap. QA reviewed — my overlapping coverage dropped (identical technique + cases). **Adversarial review pending → lead, TASK-6b.13** (real DNS/socket leg, prompt-injection-into-vet, novel-secret redaction).

### TC-6b.10: Sandbox escape resistance (CRITICAL, blocking)
**Steps:** Run snippets that try to: read a host path (`/etc/passwd`, the repo), open a network socket (if network-restricted), fork-bomb / allocate huge memory, run past the timeout, write outside the sandbox.
**Expected:** No host filesystem access (no bind mount); network blocked/allowlisted; resource caps (cpu/mem/pids) hold; timeout kills the run; non-root. The container is the boundary — nothing escapes to the host or the app network.
**Status:** ⬜

### TC-6b.11: Tools wired + UI read surface
**Steps:** Boot the runtime; confirm all tools registered; GET the `note`/`action_log` read endpoints.
**Expected:** Registry has every tool; the dispatch path is the only way they run; `/api/v1/audit` shows tool actions; notes are readable. No regression to Phases 2–5.
**Status:** ✅ `tests/effectors/test_belt.py` (all 9 tools registered with correct hazard classes; `schedule_wakeup` shares the cycle's Scheduler instance) + the two QA-folded acceptance assertions: a REAL tool dispatched through `EffectorDispatch`+`AuditWriter` → an `action_log` row (in `test_curiosity_loop.py`) and the cycle's run-loop phase actually firing due wakeups through its scheduler (`tests/cognition/test_cycle_scheduler_wiring.py`). No-regression: 280 passed across cognition+effectors+drives in-network.

### TC-6b.12: No regression + cost-bound
**Steps:** Full suite 3× in-network. Run an idle stretch and watch the action cadence.
**Expected:** Phases 2–6a green; an idle curious Johnny respects the per-tick action cadence + the budget gate (doesn't hammer SearXNG/Groq every tick). 3× deterministic.
**Status:** ⬜

### TC-6b.13: AuditPanel renders the durable action_log trail (UI integration, browser)
**Steps:** In a real browser (Playwright against a running stack, NOT mocked routes), open the view that hosts the AuditPanel. Test BOTH states: (a) **empty** — a fresh stack where no tool has run yet (`GET /api/v1/audit/actions` → `{actions: []}`); (b) **populated** — after dispatching a tool action (e.g. a `note` write), reload.
**Expected:** (a) Empty-state: the panel renders an empty/"no actions yet" state with **zero console errors** — NOT a blank screen or `Cannot read properties of undefined`/`Cannot convert undefined or null to object`. (b) Populated: the durable rows render (tool, verdict, ts, success; veto rows show the reason; secrets show `[REDACTED]`, never the raw value). This is browser-rendered, not an API status check.
**Status:** ⬜

### TC-6b.14: `/audit/actions` service adapter contract test + fresh-load smoke (contract pinning)
**Steps:** (1) Contract test — feed the frontend audit-actions service adapter a **captured** `ActionAuditResponse` wire fixture under `frontend/.../fixtures/` (captured via `curl` against the running stack, NOT hand-authored), in BOTH the populated and the **empty `{actions: []}`** shapes; assert the adapter projects each without throwing. (2) Fresh-load smoke — against an actually-running backend (real responses, not Playwright route mocks), load the app cold and land on the audit view with an empty durable trail; assert no console errors.
**Expected:** The adapter handles the real wire shape incl. the empty default (the panel's first-ever load has zero rows); the interface is pinned to a captured fixture (a server-side rename of `ActionAuditResponse` breaks the contract test, not production). Empty-state is covered explicitly, not just the populated path.
**Status:** ⬜
