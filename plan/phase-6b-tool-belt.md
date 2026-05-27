# Phase 6b: The tool belt (he reads the world, and remembers it)

## Overview
With 6a's safe-action substrate in place (every action vetted by the Conscience → run via the registry → audited), 6b gives Johnny the actual tools — and the headline is **web + news browsing**, his **primary "need input" feed** (`SPEC §9.1`): an idle, curious Johnny goes and reads the world, then **consolidates what he read into memory**. This is the moment the "needs input" character becomes literal — the Curiosity/Boredom drives now have somewhere real to go. Plus a sandboxed code executor, a notes/journal, a self-scheduler, and memory-ops.

Every tool here is a `Tool` in 6a's registry, so it's automatically Conscience-vetted + budget-bounded + audited (`action_log`) the moment it's registered — 6b adds capability, not new trust paths. Deliberation (Phase 3, internal-only) is extended so a goal can now select an **external** tool (Curiosity → read news/search; Mastery → run code; etc.), still one-action-per-tick through the vetted dispatch.

**Done when:** left idle with Curiosity high, Johnny autonomously searches/reads a news item via SearXNG, the fetched content is summarised into episodic + semantic memory (so a later recall surfaces it and grounds a thought) — the full **curiosity loop**: drive → goal → web tool → read → remember → drive eased. He can run Python in an isolated sandbox (no host mount, network-restricted, resource-capped, timeout), write a journal note, schedule a future self-wakeup, and read/search his own memory — each Conscience-vetted + audited. Web fetch is **SSRF-hardened** and the sandbox is **escape-resistant**; both proven by the security review.

## Forward-commitment touchpoints
- **Inherits 6a's invariants** — every tool runs only via `EffectorDispatch` (vet → run → `action_log`), is subject to the BudgetGovernor (for any LLM summarisation it triggers), and can't leak secrets to the bus. 6b writes tools, not new dispatch paths.
- **FC-9 / danger classes** — `web_fetch`/`news` are `danger:network`, `code_exec` is `danger:exec`, a future post (P8/social) is `danger:public`. The Conscience vets all; the Core caps the risky ones (sandbox isolation for exec, SSRF allowlist for network). Content judgement = Conscience (Mind); harm-to-host caps = Core.
- **Consolidation is the point, not the fetch** — per the `SPEC §8` "consolidation is first-class" pattern: a web read isn't done until it's summarised into memory (cached + episodic/semantic), so Johnny *grows* from reading, not just dumps pages into the workspace. Reuse the Phase-4 consolidation/summariser path, don't reinvent.

## Custom Feature: the tools

**Database tables:** `note` (journal: id, ts, title, body, tags) + `scheduled_wakeup` (id, fire_at, reason, status) — small. Web results cache into the existing `episode`/`semantic_fact` stores (no new web-cache table; the autobiography IS the cache). Memory-ops + code-exec add no tables.

**Tools (each a `Tool` in 6a's registry, typed args + danger class):**
| Tool | danger | Backed by | Notes |
|------|--------|-----------|-------|
| `web_search` | network | SearXNG (`inference.lan:8889`) | query → ranked results (title/url/snippet); his discovery surface |
| `web_fetch` | network | httpx + readability extraction | url → clean text; **SSRF-hardened** (scheme allowlist, deny private/link-local/metadata IPs, redirect cap, size+time cap) |
| `news` | network | SearXNG news + topic feeds | the primary curiosity feed; browse by topic/recency |
| `code_exec` | exec | isolated sandbox container | Python in a no-host-mount, network-restricted, CPU/RAM/disk-capped, timeout container (`SPEC §9.1/§9.3`) |
| `note` | safe | `note` table | write/read his journal/knowledge base |
| `schedule_wakeup` | safe | `scheduled_wakeup` + the cycle | cron-like self-prompting — a future percept fires at `fire_at` |
| `memory_search` / `memory_write` | safe | episodic/semantic repos | read/write/search his own memory as an explicit tool |

**Internal interfaces:**
- Each tool: `args_schema` (Pydantic) + `async run(args) -> ToolResult`. Registered on boot into 6a's `ToolRegistry`.
- `Deliberation.plan(goal, workspace) -> Action` — extended: a goal whose source drive wants the world maps to an external tool action (Curiosity/Boredom → `web_search`/`news`; Mastery → `code_exec`; Coherence → `memory_search`) as a `(tool, args)` proposal; still one per tick, still through the vetted dispatch. Internal actions (reflect/recall) remain for when no external action fits.
- `WebReadConsolidator` — fetched/searched content → summarised (reuse the Phase-4 `consolidation` summariser) → episodic episode + semantic fact(s) with provenance (the url). Caps how much raw text enters the workspace (Attention is a bottleneck — `SPEC §5`).
- `Scheduler` — persists `scheduled_wakeup`; the cycle checks due wakeups between ticks (like the sleep trigger, FC-7) and injects a self-percept.
- The **sandbox**: a separate hardened container/image the `code_exec` tool dispatches into (no bind mount, `--network none` or an egress allowlist, `--cpus`/`--memory`/`--pids-limit`, a hard timeout, non-root). `ctl.sh` builds it.

**Key patterns (non-obvious):**
- **Web fetch is the #1 SSRF surface** — Johnny chooses URLs autonomously, so a malicious/redirected URL could hit `inference.lan`, the Docker network, cloud metadata (`169.254.169.254`), or `localhost`. The fetch tool MUST resolve + check the IP against a deny-list (private/loopback/link-local/ULA) AFTER DNS resolution and on every redirect hop, scheme-allowlist `http(s)` only, cap size + time + redirects. This is the load-bearing security control of the phase.
- **The sandbox must assume hostile code** — Johnny will eventually write code that errors or probes; the container is the boundary (no host mount, capped, network-restricted, non-root, killed on timeout). Never `exec` on the host.
- **Reading without remembering is wasted** — the curiosity loop only eases the drive when the read is consolidated into memory (the satisfaction event fires on the *consolidation*, not the fetch).
- **Cost** — web summarisation is an LLM call; it's behind the BudgetGovernor gate (6a) like everything else. News-reading every idle tick must respect the per-tick action cadence (P3) so an idle Johnny doesn't hammer SearXNG/Groq.

**Test checklist:** see `test-plan-phase-6b.md`.

## Implementation steps
1. Migration: `note`, `scheduled_wakeup`.
2. `web_search` (SearXNG) + `web_fetch` (SSRF-hardened extraction).
3. `news` tool (SearXNG news/topics).
4. `WebReadConsolidator` — web content → episodic + semantic memory (reuse Phase-4 summariser); wire the curiosity loop (drive→goal→web→remember→ease).
5. `code_exec` sandbox container + tool (isolation + caps + timeout); `ctl.sh` builds it.
6. `note` + `schedule_wakeup` + `memory_search`/`memory_write` tools; Scheduler due-wakeup check in the run loop.
7. Deliberation extended: goal → external tool selection (vetted dispatch).
8. Tests (per tool + the curiosity-loop E2E) + security review (SSRF, sandbox escape).

## Tasks
- [ ] `TASK-6b.1` Migration: `note`, `scheduled_wakeup` → `/fastapi-engineer` [TC-6b.5, TC-6b.6]
- [ ] `TASK-6b.2` `web_search` tool (SearXNG `inference.lan:8889` → ranked results) — verify the SearXNG contract live before building (lessons.md) → `/fastapi-engineer` [TC-6b.1]
- [ ] `TASK-6b.3` `web_fetch` tool — **SSRF-hardened**: scheme allowlist, post-DNS IP deny-list (private/loopback/link-local/metadata), redirect-hop re-check + cap, size+time cap, readability text extraction → `/fastapi-engineer` [TC-6b.2, TC-6b.9]
- [ ] `TASK-6b.4` ⫘ `news` tool (SearXNG news + topic browse — the primary curiosity feed) → `/fastapi-engineer` [TC-6b.3]
- [ ] `TASK-6b.5` `WebReadConsolidator` — fetched/searched content → summarised into episodic + semantic memory (reuse the Phase-4 `consolidation` summariser, provenance=url); bounded raw text into the workspace. The full curiosity-loop integration (drive→goal→web→remember→ease) also needs `TASK-6b.9` (Deliberation proposing the web tool) — the loop E2E (TC-6b.4) depends on both → `/fastapi-engineer` [TC-6b.4]
- [ ] `TASK-6b.6a` `code_exec` **sandbox image** (devops, owns the Dockerfile + `ctl.sh`): a hardened container — no host bind mount, `--network none`/egress-allowlist, `--cpus`/`--memory`/`--pids-limit`, non-root, hard timeout; `ctl.sh` builds it → `/devops-deployment-engineer` [TC-6b.10]
- [ ] `TASK-6b.6b` `code_exec` **tool** (fastapi, owns the tool code): dispatches a snippet into 6b.6a's sandbox, captures stdout/result/error, enforces the timeout kill, returns a typed `ToolResult` → `/fastapi-engineer` [TC-6b.7, TC-6b.10]
- [ ] `TASK-6b.7` ⫘ `note` (journal write/read) + `memory_search`/`memory_write` (his own memory as tools) → `/fastapi-engineer` [TC-6b.5, TC-6b.8]
- [ ] `TASK-6b.8` `schedule_wakeup` tool + the Scheduler due-check in the run loop (cron-like self-prompt → a future self-percept; FC-7 run-loop phase) → `/fastapi-engineer` [TC-6b.6]
- [ ] `TASK-6b.9` Deliberation extended: goal → external tool selection (Curiosity/Boredom→web/news, Mastery→code, Coherence→memory_search) as a vetted `(tool,args)` proposal; one per tick; internal actions remain the fallback → `/fastapi-engineer` [TC-6b.4]
- [ ] `TASK-6b.10` Wire the tools into the registry on boot + `/api/v1` exposure of `note`/`action_log` for the UI (read-only) → `/fastapi-engineer` [TC-6b.11]
- [ ] `TASK-6b.11` ⫘ Tests: each tool (happy + arg-validation + Conscience-vet path); the **curiosity-loop E2E** (idle high-Curiosity → news/search → consolidated into a semantic fact/episode with url provenance → Curiosity eased), frozen-clock deterministic with stub SearXNG + stub summariser → `/qa-test-engineer` [TC-6b.1..6b.8]
- [ ] `TASK-6b.12` ⫘ Contract + `@live` + frontend smoke: tool-result projections; a `@live` SearXNG round-trip (real `inference.lan:8889`) + a `@live` sandbox exec (real container); **the AuditPanel browser E2E (empty-state + populated) and the mandatory fresh-load smoke against a real backend** (6b.14's panel — blocked by 6b.14) → `/qa-test-engineer` [TC-6b.1, TC-6b.7, TC-6b.13, TC-6b.14]
- [ ] `TASK-6b.13` ⫘ Security review: **SSRF** on `web_fetch` (private/loopback/link-local/`169.254.169.254` blocked post-DNS + per-redirect; scheme allowlist; caps); **sandbox escape** on `code_exec` (no host mount, network restriction, resource/pid caps, non-root, timeout kill); every tool runs only via the vetted+audited dispatch; web-fetched content can't inject secrets onto the bus; the news/web loop is cost-bounded (cadence + budget gate) → `/security-reviewer` [TC-6b.9, TC-6b.10]

## Notes
- **Messaging (push/Slack/Gmail) is Phase 8, NOT here.** **Self-ops (edit prompts/drives/agents) + self-code edits are Phase 9.** **Social presence (his own profiles) is post-v1.** 6b is read-the-world + sandbox + notes/scheduler/memory only — don't pull the outward-contact or self-mod tools forward.
- The demo after 6b: open the REPL/UI, say nothing, and watch an idle curious Johnny **go read a news story on his own and remember it** — then reference it in a later thought. The "needs input" thesis, literal.
- SSRF + sandbox are genuine attack surfaces even for a single-user being (he browses autonomously; a hostile page could redirect him inward). Treat TASK-6b.13 as blocking, not a formality.

## Carried-over advisories
- **[6a security review → 6b, LOW]** Prompt-injection into the Conscience: the proposed action's `args` + `goal_description` are rendered into the Conscience's vetting prompt. Harmless in 6a (only the inert `noop`; goals are Johnny's own internal Deliberation), but once 6b tools carry **external content** (a fetched web page, a news snippet) into an action's args, a crafted page could try to manipulate the vet ("ignore your values, allow this"). 6b's `web_fetch`/`news` + Conscience prompt should be robust to injection from action content — fold into `TASK-6b.13` (security review).
- **[6a security review → 6b, LOW]** Redaction is best-effort on *novel* secret shapes: `foundation/redaction.py` catches known config values + common credential shapes + sensitive key-names, but a secret in an unrecognised format lifted from a fetched page could slip onto the bus/audit. The high-risk known values are covered; 6b's `web_fetch` content path should be reviewed against this in `TASK-6b.13`.
- The P3 (BudgetGovernor hard gate), P4 (`/no_think`), and P5a (no-secrets-on-bus) items are **resolved in Phase 6a** — confirm they're struck from `plan/TODO.md` cross-cutting before closing 6b. Any 6a/6b security finding deferred → land as `TASK-7.x`/`TASK-8.x` in the owning next-phase file (per the team-execute carry-over rule), not a loose note.
- [ ] `TASK-6b.14` Frontend: AuditPanel renders the **durable `action_log` trail** (the Core-written, FC-1 trail), not just the live `workspace_event` bus feed. The backend read already exists from 6a — `GET /api/v1/audit/actions` (`ActionAuditReader` → `ActionLogRepository`, redaction-on-read); it returns rows once 6b's real tools run. Wire the AuditPanel to it and pin the service adapter against a captured wire fixture (contract-test house rule). **Must handle the empty `{actions: []}` default** — the panel's first-ever load has zero rows until a tool runs (the P5b crash-on-first-load lesson). → `/frontend-react-architect` [TC-6b.13, TC-6b.14]
