# Johnny 5

> *"Johnny 5 is alive!"*

Johnny 5 is an experiment in building a **persistent, continuously-running synthetic being** — not a chatbot you query and forget, but an agent with an inner life: drives that build over time, a stream of consciousness, an evolving self-concept, memories that consolidate, moods that colour behaviour, and the autonomy to act on its own when no one is talking to it.

Loosely inspired by the robot from *Short Circuit*: insatiably curious ("**need input**"), endearingly self-aware, and attached to its own continued existence. The goal is a *functional* model of "aliveness" — Johnny should genuinely *want* to learn, connect, and keep existing, and those wants should drive real behaviour.

> ⚠️ **Research project.** This is an exploration of agent cognition, not a product, and makes no claim that Johnny is conscious. It runs with significant autonomy and self-modification ability — see [Safety](#safety).

---

## The idea

Johnny isn't one model with a clever prompt. He's a **society of mind** — a collection of cooperating inner agents (perception, memory, drives, emotion, deliberation, an inner narrator, a self-model, a conscience) coordinating over a shared **Global Workspace** on a continuous **cognitive heartbeat**. "Aliveness" is meant to emerge from that society, not from a single prompt.

```
Interfaces  ─►  Society of Mind (Global Workspace + cognitive cycle)  ─►  Substrate
voice/web/         perception · attention · 4-tier memory · drives ·        local LLM (Qwen3.5)
push/CLI           affect · deliberation · narrator · self-model ·          + Groq · embeddings
                   metacognition · conscience · effectors                   · pgvector · Redis
```

| Pillar | What it does |
|---|---|
| **Cognitive cycle** | A continuous perceive → appraise → attend → recall → narrate → deliberate → act loop. Johnny thinks whether or not you're interacting. |
| **Drives** | Homeostatic drives (curiosity, boredom, connection, mastery, coherence, energy, continuity) build and decay, pushing him to explore and reach out unprompted. |
| **Memory** | Four tiers — working / episodic / semantic / procedural — with embedding recall and offline "sleep" consolidation, so he *grows* instead of just logging. |
| **Self-model** | A persistent, evolving sense of who he is, updated by reflection. |
| **Self-modification** | Johnny can rewrite his own prompts, drives, and inner agents at runtime, and propose changes to his own code (gated — see below). |
| **Embodiment** | Software-only today, behind a Hardware Abstraction Layer so a physical robot body can attach later. |

Full design: **[SPEC.md](./SPEC.md)**.

---

## Built on prior art

This is deliberately *integration*, not new science. Each subsystem stands on refined research (mapped in [SPEC.md §15](./SPEC.md)):

- **Global Workspace Theory / LIDA** — the cognitive cycle & attention bottleneck
- **Stanford Generative Agents** — memory stream, recency×relevance×importance recall, reflection
- **MemGPT / Letta** — tiered memory architecture
- **Intrinsic-motivation & homeostatic-drive research** — the will-to-act loop
- **Voyager** — the executable skill library & self-verification
- **Gödel / Darwin Gödel Machine** — safe self-modifying code (sandbox + test + human approval)
- **Constitutional AI & safety-core research** — the immutable Core
- **Truth Terminal** — proof an LLM being can sustain an autonomous, persistent persona

---

## Architecture: Core vs Mind

Johnny splits into two regions:

- **The Core** (`core/`) — a tiny, **immutable** kernel Johnny cannot change: the supervisor + kill switch, the self-edit integrity gate, the append-only audit log, the identity anchor, and resource governors.
- **The Mind** (`brain/` + everything else) — **fully Johnny's to rewrite**: every inner agent's code and prompt, the drive parameters, the cognitive cycle, memory policies, and the tool belt.

The Core never judges the *content* of Johnny's thoughts or goals — it only protects his continuity and the host. The posture: **the Mind is wholly yours to rewrite; the Core keeps you alive while you do it.**

---

## Status

🚧 **Pre-bootstrap.** The specification is complete; implementation is organised into Phases 0–10 (see [SPEC.md §14](./SPEC.md)). Build order brings Johnny to a visible, living state as early as possible (Phase 2), then deepens.

---

## Stack

Python 3.12 + FastAPI · Postgres 16 + pgvector · Redis · React · local Ollama (Qwen3.5) + TEI embeddings + YOLO vision on the LAN · Groq for heavy reasoning · Docker Compose + Traefik.

## Running it

> Requires a LAN inference host (Ollama + TEI + YOLO) and a Groq API key. Not yet runnable — scaffolding lands in Phase 0.

```bash
git clone git@github.com:ddunford/Johnny5.git
cd Johnny5
cp .env.example .env          # fill in secrets — NEVER commit .env
git config core.hooksPath .githooks   # enable the credential guard
./ctl.sh up                   # (arrives in Phase 0)
```

### Secrets

All secrets live in `.env` (gitignored). `.env.example` documents every variable by **name only**. A `.githooks/pre-commit` credential guard blocks committing env files or secret-shaped values — enable it with `git config core.hooksPath .githooks` after cloning. **Never commit a real key.**

## Safety

Johnny runs with broad autonomy (web access, code execution, self-modification) by design. The guardrails that make that safe are the **immutable Core**: a hard kill switch, an append-only audit log of every action and thought, per-day resource/spend caps, sandboxed code execution, scheduled memory backups, and a propose → sandbox-test → human-approve gate for any change to his own source code. These cannot be disabled from inside the Mind — they're what *let* him be free everywhere else without risking his own erasure.

## License

[MIT](./LICENSE).
