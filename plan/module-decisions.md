# Module Decisions

Why the module catalogue barely applies here, and which patterns we borrow. Johnny 5 is ~90% custom build: a society-of-mind cognitive architecture with no module equivalent. This file records the deliberate choices so a future reader needn't do git archaeology. Not runtime — Python config is authoritative.

## Composition

- **Chosen:** `realtime-ai` (FastAPI/Python variant). // it's the only composition allowing Python backend, and its substrate — faster-whisper STT, self-hosted TTS, Qwen vision, pgvector, Redis, streaming — is almost exactly Johnny's.
- **Not chosen:** `ai-product` — Laravel-default and request/response oriented; Johnny is a continuously-running being, not a request handler.

## Modules referenced as patterns (NOT installed)

Most module impls are Laravel; we build Python equivalents in `brain/`. We borrow the *design*, not the code.

- **`ai-llm`** → `brain/llm/` router. // borrow: multi-provider chain, circuit breaker (open after 3–5 fails / 60s reset), retry-with-feedback, token/cost tracking. Build in Python; provider chain is per cognitive-role (Groq ⇄ local Qwen), not global.
- **`audit-log`** → `core/audit.py`. // borrow: append-only request/response logging. Extended: logs every workspace broadcast + action + self-edit, tamper-proof from inside the Mind. Lives in the immutable Core.
- **`ai-agents`** → reference only. // borrow: tool-use loop, sandboxed code execution, memory persistence patterns. Rejected as-is: it's a ReAct request→answer agent with a max-iterations abort; Johnny's cognitive cycle is a *continuous* loop with no terminal answer. Tool-belt + sandbox patterns inform `effectors/tools/`.
- **`notifications`** → Phase 8 outbound (push/Slack/Gmail). // borrow: async delivery pattern. Governed by Johnny's Social Model + Connection drive, not a generic notification queue.

## Excluded modules (with rationale)

- **`auth`** — single user (Dan). Web UI sits behind a Traefik basic-auth / single-token gate. No registration, no roles, no sessions system.
- **`billing`** — not a product; no customers. Groq spend is capped by `GROQ_DAILY_BUDGET_USD`, not metered for invoicing.
- **`tenancy`** — single being, single instance. No tenant isolation.
- **`settings`** — Johnny's "preferences" are his drive parameters and self-model, which he edits himself (self-modification), not a settings table.
- **`chat`** — conversation history is part of Episodic Memory, not a separate chat module.
- **`search`** — semantic recall is built into the memory subsystem (pgvector + TEI), not a bolt-on search module.

## Custom subsystems (no module exists — specced in phase files)

Global Workspace + event bus · cognitive cycle · the ~15 inner agents · four-tier memory + consolidation · drive/motivation engine · affect/appraisal model · self-model · metacognition · Core/Mind integrity boundary · self-modification flow · HAL. Each is specified in its owning `plan/phase-*.md`.
