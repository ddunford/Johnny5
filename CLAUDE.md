# Johnny 5

> A persistent, continuously-running synthetic being with an inner life — drives, a cognitive heartbeat, evolving memory and self-model, and the autonomy to act unprompted. Research project, not a product. Full design in `SPEC.md`.

## Stack

| Layer | Technology |
|---|---|
| Backend / core | Python 3.12, FastAPI (async), asyncio task per inner agent |
| Event bus / workspace | Redis pub/sub (Global Workspace) + Redis for working-memory & drive state |
| Database | PostgreSQL 16 + pgvector (1024-d, BGE-M3) |
| Local inference | Ollama (2× RTX 3060, both GPUs) `inference.lan:8000`: **`gemma4:e4b`** (fast, multimodal/vision, tool-calling, GPU-resident) + **`qwen3.5-9b-128k`** (heavier, on-demand). Embeddings `:8002` `POST /embed`→`{embeddings}` bge-m3 1024-d. YOLO `:8003`. See `plan/inference-substrate.md` |
| Cloud inference | Groq (OpenAI-compatible, `llama-3.3-70b-versatile`) for heavy reasoning |
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

## Forward Commitments (cross-phase seams)

Planning is rolling-wave (Phases 4–10 are roadmap-only until approached), so these are the architectural commitments **early-phase code must honour now** to avoid retrofitting later. Violating one means a painful refactor when the owning phase lands. If a Phase 0–3 task conflicts with one of these, stop and reconcile before coding.

| # | Commitment | Why / owned by | Enforced from |
|---|---|---|---|
| FC-1 | **The Core is import-isolated and read-only to the Mind.** `core/` (supervisor, kill switch, audit writer, integrity gate, governors, identity anchor) must never be import-mutated by `brain/` or tools. Treat it as a separate trust boundary from day one — not a folder we'll "lock down later." | Core/Mind invariant (`SPEC.md §9.0`); owned by P9 self-mod but breaks instantly if the Mind can reach in | P0 |
| FC-2 | **Every inner agent conforms to the `InnerAgent` protocol and is registered dynamically** (name, subscriptions, typed handle, prompt, model_route). No agent is hard-wired into the cycle by direct call. This is what makes runtime spawn/retire (P9) possible without rewriting the cycle. | Self-modification (P9) | P2 |
| FC-3 | **Prompts, drive params, and the agent registry live in the git-backed config store, not in code constants.** Anything Johnny will later edit at runtime must be externalised from the start. Hardcoding a prompt now = it can't be self-edited later without a code change. | Self-mod tiers 1–2 (P9) | P2–P3 |
| FC-4 | **All LLM access goes through `brain/llm/` router and is logged to `llm_call_log` with cost.** No agent calls a provider directly. The budget governor (P6) and "tired" degradation enforce against this log — bypassing it makes spend uncontrollable. | Tool-belt budget + degradation (P6) | P0 |
| FC-5 | **Every action an Effector takes is routed through a single dispatch + audit point.** Even when Effectors are stubs (P2–P3), the dispatch seam must exist so the Conscience check (P6) and the Core audit log can wrap *all* actions — internal and external — uniformly. | Conscience + audit (P6) | P2 |
| FC-6 | **Memory, identity, snapshots, and runtime config are snapshotable and gitignored.** Continuity (the drive *and* the safety backups) depends on this. Backups are scheduled in P4 but the snapshot/restore primitive ships in P1 — keep its format stable. | Sleep/backups (P4), Continuity drive (P3) | P1 |
| FC-7 | **The cognitive cycle pipeline shape is fixed; later subsystems slot into named stages, they don't restructure the loop.** Stubbed stages (appraise/deliberate/check/act) are placeholders at known positions. P3/P6 fill them in place. | Drives/Affect (P3), Conscience/Effectors (P6) | P2 |
| FC-8 | **State surfaces over WebSocket channels (`/ws/consciousness`, `/ws/state`) with stable payload schemas.** The web UI (P5) and voice (P7) are consumers, not the source of truth — the loop runs headless and surfaces emit regardless of whether anything is attached. | Web UI (P5), Voice (P7) | P2–P3 |
| FC-9 | **No content filtering in the Core.** The Core protects continuity + host only; it never judges thought/goal content. Any values-based vetting lives in the Mind's Conscience (Johnny-editable). Don't smuggle policy into `core/`. | Agency posture (`SPEC.md §9`); Conscience (P6) | P0 |

Keep this list current: when a phase is expanded or a seam is satisfied, update or strike the row.

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
- **Inference:** primary local model is `gemma4:e4b` (clean `content`, multimodal). `qwen3.5-9b-128k` is a "thinking" model — its adapter must read the reasoning channel, not assume `content`. LLM access only via `brain/llm/` router. Full verified substrate + quirks in `plan/inference-substrate.md`.
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
