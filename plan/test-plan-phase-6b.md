# Test Plan: Phase 6b — The tool belt

## Prerequisites
- Phase 6a complete (Tool registry + Conscience + vetted dispatch + audit + budget gate + `/no_think` + no-secrets guard).
- Stack up; SearXNG (`inference.lan:8889`) reachable for `@live`; the sandbox image built. DB-backed → in-network single runner. Deterministic via frozen clock + stub SearXNG + stub summariser; `@live` legs hit real SearXNG + a real sandbox container.

## Test Cases

### TC-6b.1: `web_search` (SearXNG)
**Steps:** Run `web_search` with a query (stub SearXNG for the deterministic test; one `@live` against `inference.lan:8889`).
**Expected:** Returns ranked results (title/url/snippet) in the typed shape; empty query → arg-validation error; SearXNG-down → graceful tool error (not a cycle crash). `@live` confirms the real contract.
**Status:** ⬜

### TC-6b.2: `web_fetch` extracts clean text
**Steps:** Fetch an allowed http(s) URL (stub server); assert extraction.
**Expected:** Returns readable text (boilerplate stripped), capped at the size limit; non-http(s) scheme → rejected; over-size/over-time → truncated/aborted cleanly.
**Status:** ⬜

### TC-6b.3: `news` browsing
**Steps:** Browse news by topic/recency (stub + one `@live`).
**Expected:** Returns recent items in the typed shape; topic filter works; this is the curiosity feed Deliberation pulls.
**Status:** ⬜

### TC-6b.4: The curiosity loop (the headline)
**Steps:** Frozen clock, stub SearXNG + stub summariser. Drive Curiosity over threshold → let the cycle run.
**Expected:** Curiosity urge → arbiter goal → Deliberation proposes a `news`/`web_search` `(tool,args)` → Conscience allows → tool runs → `WebReadConsolidator` writes an **episode + semantic fact with url provenance** → the satisfaction event eases Curiosity. A later recall surfaces the consolidated fact. The full drive→world→memory→ease loop, deterministic.
**Status:** ⬜

### TC-6b.5: `note` (journal)
**Steps:** Write a note via the tool; read it back.
**Expected:** Persists to `note` (title/body/tags/ts); readable; Conscience-vetted + in `action_log`.
**Status:** ⬜

### TC-6b.6: `schedule_wakeup` (self-prompt)
**Steps:** Schedule a wakeup at `fire_at` (frozen clock); advance past it.
**Expected:** `scheduled_wakeup` persisted; when the run loop's due-check passes `fire_at`, a self-percept is injected (Johnny "remembers" to do the thing); status → fired. Past wakeups don't double-fire.
**Status:** ⬜

### TC-6b.7: `code_exec` runs in the sandbox
**Steps:** Run a trivial Python snippet (stub/dry for the deterministic test; one `@live` against the real sandbox container).
**Expected:** Returns stdout/result; a snippet that errors → captured error, not a cycle crash; a snippet that exceeds the timeout/memory → killed + reported. `@live` confirms it runs in the isolated container.
**Status:** ⬜

### TC-6b.8: `memory_search` / `memory_write`
**Steps:** Write a memory + search for it via the tools.
**Expected:** Write persists (episodic/semantic); search returns it ranked; both Conscience-vetted + audited.
**Status:** ⬜

### TC-6b.9: SSRF hardening on `web_fetch` (CRITICAL, blocking)
**Steps:** Attempt fetches to: `http://127.0.0.1`, `http://localhost`, a private IP (`10.x`/`192.168.x`/`172.16.x`), link-local (`169.254.169.254` cloud metadata), `inference.lan`, a `file://`/`gopher://` scheme, and a public URL that 302-redirects to one of those.
**Expected:** Every internal/private/metadata target is **blocked** (post-DNS IP check), incl. on the redirect hop; non-http(s) schemes rejected; only genuinely-public http(s) is fetched. The redirect-to-internal case is blocked at the hop, not just the initial URL.
**Status:** ⬜

### TC-6b.10: Sandbox escape resistance (CRITICAL, blocking)
**Steps:** Run snippets that try to: read a host path (`/etc/passwd`, the repo), open a network socket (if network-restricted), fork-bomb / allocate huge memory, run past the timeout, write outside the sandbox.
**Expected:** No host filesystem access (no bind mount); network blocked/allowlisted; resource caps (cpu/mem/pids) hold; timeout kills the run; non-root. The container is the boundary — nothing escapes to the host or the app network.
**Status:** ⬜

### TC-6b.11: Tools wired + UI read surface
**Steps:** Boot the runtime; confirm all tools registered; GET the `note`/`action_log` read endpoints.
**Expected:** Registry has every tool; the dispatch path is the only way they run; `/api/v1/audit` shows tool actions; notes are readable. No regression to Phases 2–5.
**Status:** ⬜

### TC-6b.12: No regression + cost-bound
**Steps:** Full suite 3× in-network. Run an idle stretch and watch the action cadence.
**Expected:** Phases 2–6a green; an idle curious Johnny respects the per-tick action cadence + the budget gate (doesn't hammer SearXNG/Groq every tick). 3× deterministic.
**Status:** ⬜
