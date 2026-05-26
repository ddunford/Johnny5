# Johnny 5

> A persistent, continuously-running synthetic being with an inner life — drives, a cognitive heartbeat, evolving memory and self-model, and the autonomy to act unprompted. Research project, not a product. Full design in `SPEC.md`.

## Stack

| Layer | Technology |
|---|---|
| Backend / core | Python 3.12, FastAPI (async), asyncio task per inner agent |
| Event bus / workspace | Redis pub/sub (Global Workspace) + Redis for working-memory & drive state |
| Database | PostgreSQL 16 + pgvector (1024-d, BGE-M3) |
| Local inference | Ollama Qwen3.5 9B (text+vision) `inference.lan:8000/8001`, TEI embeddings `:8002`, YOLO11 `:8003` |
| Cloud inference | Groq (OpenAI-compatible, Llama 3.3 70B) for heavy reasoning |
| Frontend | React 19 + Vite (TypeScript), WebSocket for live consciousness/state streams |
| Voice | faster-whisper (STT), Piper (TTS), openWakeWord — Phase 7 |
| Config store | git-backed (versioned prompts, drive params, inner-agent registry) |
| Ops | Docker Compose + Traefik (`traefik_demosrv`, certresolver `le`), `ctl.sh` |

## Architecture Decisions

### Application structure
- **Modular monolith**, one Python package per cognitive subsystem. The defining invariant is the **Core/Mind boundary** (`SPEC.md §9.0`): `core/` is immutable and read-only to the running Mind (`brain/` + tools); the Mind is fully self-modifiable. Nothing in the Mind may import-mutate the Core.
- **Event-driven, not call-graph.** Inner agents never call each other directly — they publish/subscribe over the Global Workspace (Redis pub/sub). This keeps the society loosely coupled, lets Johnny add/retire inner agents at runtime, and makes the entire inner life observable (every broadcast is logged + streamable).
- The **cognitive cycle** (`brain/cycle.py`) is the orchestrator: a continuous async loop, rate-modulated by affect. Most inner agents are event-driven and cheap; heavy ones (Deliberation, Metacognition, Self-Model) fire on a slower sub-cadence to control cost.

### Backend patterns
- Each **inner agent** is a module with a typed contract (Pydantic in → Pydantic out), its own prompt (git-backed, runtime-editable), and a model route. New agents conform to a shared `InnerAgent` protocol so they can be spawned/registered dynamically.
- **LLM access only via the router** (`brain/llm/`) — per-role provider chain (Groq ⇄ local Qwen), circuit breaker (open after 3–5 fails, 60s reset), retry-with-feedback on schema-validation failure. Never call a provider directly from an agent.
- **Repository pattern** over Postgres+pgvector for all memory access (behind interfaces — memory is the most-tested subsystem).
- **Exception-based** error handling. Provider/circuit failures degrade gracefully ("tired" → local-only) rather than crash the cycle.
- **Contract test per LLM-role adapter** (house rule): every adapter that parses a model response has a `ServerEnvelope`-style fixture + test asserting the projection, so a model output-shape change can't silently break cognition.
- Determinism for tests via a **frozen-clock cycle harness** — the cognitive loop must be steppable and reproducible with injected time + mocked providers.

### Frontend patterns
- **Feature-based** structure. **React Query** for request/response server state; **Zustand** for the live streamed state (consciousness feed, drive levels, mood) fed by WebSocket.
- Service classes wrap the API/WebSocket, consumed by hooks. Forms (self-edit approval, config) via React Hook Form + Zod.
- Error boundaries per panel with a global fallback (a dead panel must not kill the live consciousness view).

### Cross-cutting
- **Single user (Dan), single being.** No auth/billing/tenancy modules. The web UI sits behind a Traefik basic-auth / single-token gate — not a user system.
- **Self-modification tiers** (`SPEC.md §9.2`): prompt/drive/agent edits are free at runtime (git-versioned, revertible); structural changes auto-checkpoint; **self-code edits go through a Core-enforced propose→sandbox-test→human-approve gate** — the one hard gate.
- **Continuity safeguards live in the Core** and cannot be disabled from the Mind: kill switch, append-only audit log, per-day token/$ budget, sandboxed code execution, scheduled memory/identity backups.
- **Privacy:** Johnny's memory, identity, snapshots, and runtime config are gitignored — never pushed to the public repo.

## Modules in Use

This project is ~90% custom build. Closest composition: **`realtime-ai`** (FastAPI variant). Module patterns referenced (not installed wholesale — most are Laravel impls, we build Python equivalents): `ai-llm` (router/circuit-breaker pattern), `audit-log` (Core audit), `notifications` (Phase 8 push). See `plan/module-decisions.md`. Excluded: `auth`, `billing`, `tenancy`, `settings` (single-user single-being — no need).

## Non-Obvious Domain Patterns

> Fill in as subsystems ship. Candidates already known from the spec:
- **Attention is a real bottleneck, by design.** Per LIDA/GWT research, flooding the workspace/context with low-salience input *degrades* decisions. The Attention agent must select, not pass through — do not "just put everything in the prompt."
- **Consolidation is first-class.** MemGPT/Letta's known weakness is no automatic consolidation. Johnny's "sleep" cycle (episodic→semantic summarisation, decay, self-model refresh) is what makes him *grow* rather than accumulate logs. It is not optional polish.
- **The Core never judges thought content.** It only protects continuity + the host. Resist adding content filtering to the Core — that belongs (if anywhere) in the Mind's Conscience, which Johnny can edit.

## Conventions

- **API base:** `/api/v1/`. WebSocket: `/ws/consciousness`, `/ws/state`.
- **Auth:** single-token / Traefik basic-auth gate on the web UI. No user system.
- **Env:** `.env` (see `.env.example`). `.env.testing` with `POSTGRES_DB=johnny5_test`. Never the dev DB for tests.
- **Inference:** always prefix system prompts with `/no_think` for Qwen structured output. LLM access only via `brain/llm/` router.
- **Git hooks:** `.githooks/pre-commit` credential guard. `git config core.hooksPath .githooks` after clone.
- **Privacy:** never commit `data/`, `memory/`, `snapshots/`, `config/runtime/`, or any `.env`.
- **Naming:** no phase numbers or plan metadata in code. Inner agents named for their cognitive role.

## Tracker

Open work, in order: **`plan/TODO.md`** — single source of truth. Move items between *In progress* / *Next* / *Left*; delete on completion. Per-phase progress lives in that phase's plan file via `[ ]`/`[x]` checkboxes.

Planning is **rolling-wave**: Phases 0–3 are fully tasked in `plan/phase-*.md`. Phases 4–10 live at roadmap fidelity in `plan/TODO.md` and get expanded into full phase files (re-run `/bootstrap-from-spec` scope or hand-write) as each approaches.

## Out of Scope (v1)

- Physical robot body — HAL contract lands Phase 10; the build itself is post-v1.
- Multi-user / multi-tenancy / SaaS.
- Outward social presence (his own Facebook/LinkedIn/X profiles) — staged, post-v1 (`SPEC.md §9.1`).
- Training/fine-tuning base models — we orchestrate existing ones.
- Dreaming as generative recombination, long-horizon multi-day projects, a second peer instance — post-v1 stretch.
