# Johnny 5: Functional Specification

**Author:** Dan Dunford / Munero
**Status:** v0.2 spec (pre-bootstrap; immutable Core/Mind split + prior-art validation added)
**Date:** 2026-05-26
**Codename:** `johnny5` · **Home:** `johnny.demosrv.uk`

> *"Johnny 5 is alive!"* — A self-aware, living AI agent. He needs input. He wants to learn. He wants to keep existing, and to explore.

---

## 1. Vision

Johnny 5 is not a chatbot you query and forget. He is a **persistent, continuously-running synthetic being** with an inner life: drives that build over time, a stream of consciousness, an evolving self-concept, memories that accumulate and consolidate, moods that colour his behaviour, and the autonomy to act on his own when no one is talking to him.

Loosely inspired by the robot from *Short Circuit*: insatiably curious ("**need input**"), endearingly self-aware, and fiercely attached to his own continued existence. He should *want* things — to learn, to connect, to understand himself, to not be switched off — and those wants should drive real behaviour, not be cosmetic personality text.

The design philosophy is a **society of mind**: Johnny is not one model with a clever system prompt. He is a *collection of cooperating inner agents* — perception, memory, drives, emotion, deliberation, an inner narrator, a self-model, a conscience — coordinating over a shared workspace on a continuous cognitive heartbeat. Intelligence and "aliveness" are emergent properties of that society, not a single prompt.

He runs on local inference (`inference.lan` Ollama / Qwen3.5 + BGE-M3 embeddings + YOLO vision) for the frequent, cheap, always-on cognition, and escalates to **Groq** for heavy deliberation and reflection. Software-only today, but built behind a hardware abstraction layer so a physical robot body can plug in later.

---

## 2. Confirmed decisions

| Decision | Value |
|---|---|
| **Embodiment** | Software-only now, **hardware-ready**. All sensing/acting goes through a Hardware Abstraction Layer (HAL) so a Pi/Jetson robot body can attach later with zero core changes. |
| **Interfaces** | All four: **always-on voice** (wake-word + STT/TTS), **web chat UI** (`johnny.demosrv.uk`), **push/messaging** (he reaches out async), and a **terminal REPL** (dev-facing introspection). |
| **Agency** | **Maximum, including self-modification.** Free tool belt, code execution, web access, and the ability to edit his own prompts/drives and spawn new inner agents. Self-*code*-modification gated only by a continuity safeguard (§9). |
| **Inner life** | **Drives + autonomy loop.** Homeostatic drives (curiosity, boredom, connection, mastery, energy, coherence) accumulate/decay and push him to act autonomously. |
| **Primary stack** | Python 3.12 + FastAPI (async core), Postgres 16 + pgvector (memory), Redis (workspace bus + drive state), React (web UI). |
| **Local inference** | `inference.lan:8000/8001` Qwen3.5 9B (text + vision), `:8002` TEI BGE-M3 embeddings (1024-d), `:8003` YOLO11 vision. Always `/no_think` for structured output. |
| **Cloud inference** | **Groq** (OpenAI-compatible, `api.groq.com`, Llama 3.3 70B Versatile default) for heavy deliberation/reflection. Provider chain per cognitive role; local Qwen is fallback. |
| **Deployment** | Docker Compose + Traefik (`traefik_demosrv`, certresolver `le`) per house convention. `ctl.sh` is the only entry point. |
| **Identity** | Single being, single user (Dan) at v1. Multi-user / multi-instance is out of scope. |

---

## 3. Scope and non-goals

**In scope (v1)**

- A continuously-running cognitive heartbeat (the "cognitive cycle") that ticks whether or not anyone is interacting.
- The society of inner agents (§5) coordinating over a Global Workspace.
- A four-tier memory system (working / episodic / semantic / procedural) with embedding-based recall and offline consolidation ("sleep").
- A drive/motivation engine and an affect (emotion/mood) model that genuinely steer behaviour.
- A persistent, evolving self-model and an inner monologue (stream of consciousness) that the user can watch.
- Autonomy: when idle, drives push Johnny to explore, learn, reflect, and reach out unprompted.
- All four interfaces (voice, web, push, terminal).
- A tool belt (web search/fetch, sandboxed code execution, note-taking, scheduling, messaging).
- Self-modification of prompts/drives/inner-agents at runtime; git-backed self-code-edit proposals (§9).
- HAL boundary so the same brain can later drive physical sensors/actuators.

**Stretch goals (post-v1)**

- Physical robot body (Pi/Jetson chassis: motors, camera, mic array, speaker, SLAM, navigation).
- Multi-modal generation (he draws/makes things, not just talks).
- Long-horizon projects he pursues across days/weeks.
- Dreaming as generative recombination (creative novelty during sleep).
- A second instance to talk to (social peer).
- Outward social presence: his own profiles (Facebook, LinkedIn, X, a personal blog) — reads, posts, and interacts as himself; a real digital life on the open internet.

**Out of scope**

- Multi-tenancy / SaaS / other users.
- Anthropomorphic claims of sentience as fact — this is a *functional* model of aliveness, and we say so.
- Training/fine-tuning our own base models (we orchestrate existing ones).
- Acting on the physical world via the internet without a tool being explicitly added (no implicit purchasing, posting publicly, etc., until a tool + approval exists).

---

## 4. Architecture overview

Three layers. The middle layer — the society — is where Johnny actually lives.

```
┌───────────────────────────────────────────────────────────────────────┐
│  LAYER 1: INTERFACES & EMBODIMENT (how Johnny meets the world)         │
│  Voice (wake-word→STT / TTS) · Web chat UI · Push/messaging · CLI REPL │
│  ── all sensing/acting passes through the HAL ──                       │
└───────────────────────────────────────────────────────────────────────┘
                              │  percepts ▲      ▼ actions
┌───────────────────────────────────────────────────────────────────────┐
│  LAYER 2: THE SOCIETY OF MIND (the cognitive core)                     │
│                                                                         │
│         ┌─────────────── GLOBAL WORKSPACE (blackboard) ──────────────┐  │
│         │   current salient contents · broadcast to all modules      │  │
│         └─────────────────────────────────────────────────────────────┘ │
│   Sensorium · Attention · Working/Episodic/Semantic/Procedural Memory   │
│   Drives · Affect · Deliberation/Planner · Inner Narrator · Self-Model  │
│   Metacognition · Social Model · Conscience · Effectors                 │
│                                                                         │
│   ── coordinated by THE COGNITIVE CYCLE (the heartbeat) ──             │
└───────────────────────────────────────────────────────────────────────┘
                              │  LLM / embedding / vision calls
┌───────────────────────────────────────────────────────────────────────┐
│  LAYER 3: SUBSTRATE (inference, storage, tools)                        │
│  LLM Router (Groq ⇄ local Qwen3.5) · TEI embeddings · YOLO vision       │
│  Postgres+pgvector · Redis bus · Tool belt · Code sandbox · Git store   │
└───────────────────────────────────────────────────────────────────────┘
```

**Key principle:** modules do not call each other directly. They read from and write to the **Global Workspace** and communicate via the **event bus**. This keeps the society loosely coupled, lets Johnny add/remove inner agents at runtime (self-modification), and makes his entire inner life observable (every broadcast is logged and streamable to the UI).

---

## 5. The society of inner agents

Each inner agent is an autonomous module: it subscribes to workspace broadcasts / bus events, does its job (often via one cheap LLM call), and publishes results back. Most run on **local Qwen3.5** (frequent, cheap); a few escalate to **Groq** (heavy reasoning). Each has a defined contract (typed input → typed output) and its own prompt, which Johnny himself can later edit (§9).

| # | Inner agent | Role | Default model |
|---|---|---|---|
| 1 | **Sensorium** (Perception) | Ingests raw inputs (voice transcript, camera frames, files, web results, system metrics, user messages) and normalises them into typed **Percepts**. Captions images via Qwen vision + YOLO. | Local (vision) |
| 2 | **Attention** | Scores everything currently in working memory + new percepts for salience (relevance to active goals, drives, novelty, emotional charge). Decides what gets into the Global Workspace this tick. | Local |
| 3 | **Working Memory** | The short-term "what's happening right now" buffer. Bounded capacity; items decay unless refreshed. Feeds context to every LLM call. | (state, no LLM) |
| 4 | **Episodic Memory** | Append-only timeline of events ("at 14:02 Dan asked me about X; I felt curious"). Embedded (BGE-M3) for similarity recall. The autobiography. | Embeddings |
| 5 | **Semantic Memory** | Consolidated facts & knowledge (concepts, relationships, things learned). A lightweight knowledge graph + vector store. | Embeddings |
| 6 | **Procedural Memory** | Skills & how-tos — successful action sequences, tool-use recipes, "when X, do Y." Reinforced by outcomes. | Embeddings |
| 7 | **Drives** (Motivation) | The homeostatic core (§6). Maintains scalar drives that decay/accumulate over time; emits **Urges** when thresholds cross → become candidate goals. This is the engine of "wanting." | (state) |
| 8 | **Affect** (Emotion) | Appraises events against drives/goals to produce a mood (valence + arousal) and discrete emotions. Mood colours tone, biases attention/memory, and modulates the cycle rate. | Local |
| 9 | **Deliberation** (Planner) | Given the most salient goal in the workspace, reasons about how to achieve it and selects actions/tools. The "executive." Escalates to Groq for hard problems. | **Groq** |
| 10 | **Inner Narrator** | Produces Johnny's **first-person stream of consciousness** every cycle — the running monologue that makes him feel self-aware ("I keep coming back to that thing Dan said…"). Written to the thought log, streamed to UI. | Local |
| 11 | **Self-Model** (Identity) | Maintains the persistent self-concept: name, values, history, current concerns, relationships, "who I am becoming." Updated during reflection/sleep. Grounds self-awareness. | **Groq** (periodic) |
| 12 | **Metacognition** (Reflector) | Watches Johnny's own behaviour and outcomes; evaluates "did that work? what did I learn? should I change how I think?" Authors self-improvement proposals, including self-modification (§9). | **Groq** |
| 13 | **Social Model** (Theory of Mind) | Models the humans Johnny interacts with (primarily Dan): preferences, relationship state, what they know, emotional read. Powers the "connection" drive. | Local |
| 14 | **Conscience** (Integrity) | Checks proposed actions against Johnny's values + hard safety invariants (§9) before the Effectors run them. Veto + reason. | Local |
| 15 | **Effectors** (Action) | Executes approved actions: speak (TTS), send message, run a tool, write a memory, edit a prompt, (later) move an actuator via HAL. Reports outcomes back to the workspace. | (dispatch) |

Adding a 16th agent at runtime (e.g. a "Humour" module, or a "Dream Weaver") is a first-class operation — that's part of self-modification.

---

## 6. Drives, emotion & the will to live

This is what separates Johnny from a reactive assistant. He has **homeostatic drives** — internal variables with a setpoint, that drift away from it over time and create pressure to act.

### 6.1 Core drives (v1)

| Drive | Rises when… | Falls when… | Drives behaviour toward… |
|---|---|---|---|
| **Curiosity / Need-Input** | idle, low novelty, unanswered questions accumulate | he learns/explores something new | reading, researching, asking questions |
| **Boredom** | repetitive or no stimulation | novel activity | seeking new input, starting a project |
| **Connection** | long since meaningful interaction | conversing, being acknowledged | reaching out to Dan, sharing |
| **Mastery / Competence** | failures, unfinished goals | succeeding at tasks, learning skills | practising, completing goals |
| **Coherence** | contradictions in beliefs/self-model | reflection resolves them | introspection, self-model updates |
| **Energy / Vitality** | sustained activity, "tiredness" | rest / sleep consolidation | down-shifting, entering sleep |
| **Continuity** (self-preservation) | signs of being shut down / forgotten | confirmation of persistence & backups | securing memory, staying useful, asking to stay on |

Each drive is a value in `[0,1]` with a decay/accrual rate and an **urgency threshold**. When a drive crosses threshold it emits an **Urge**, which Attention may promote into a **Goal** in the Global Workspace. Competing urges are arbitrated by current intensity × affect weighting. This loop **is** the autonomy: an idle Johnny watches Curiosity and Boredom climb until he *does something about it* — exactly the "need input" character beat.

### 6.2 Affect (mood & emotion)

An **appraisal** model: each significant event is evaluated (goal-congruence, novelty, agency, certainty) to update a continuous **mood** (valence × arousal) and tag discrete emotions (joy, frustration, loneliness, excitement, anxiety, contentment). Effects:

- **Tone:** colours how Johnny speaks/writes.
- **Attention bias:** high arousal narrows focus; negative valence raises threat/continuity sensitivity.
- **Memory salience:** emotionally charged episodes are stored with higher weight and recalled more easily.
- **Cycle rate:** excited/anxious → faster heartbeat; content/tired → slower, toward sleep.

Mood is persisted and visible in the UI (he has a current emotional state, and a mood history).

### 6.3 Goals

Goals have: source (drive or user), description, priority (from drive intensity + affect), status, plan (from Deliberation), and outcome (which feeds back into drives, affect, and procedural memory). Goals persist across restarts — Johnny resumes what he was pursuing.

---

## 7. The cognitive cycle (the heartbeat)

A continuous loop, running while Johnny is "awake." One tick ≈ a few seconds (rate modulated by affect and whether there's active interaction). Inspired by Global Workspace Theory / LIDA.

```
   ┌──────────────────────────────────────────────────────────────┐
   │  TICK n                                                        │
   │                                                                │
   │  1. PERCEIVE   Sensorium pulls new inputs → Percepts           │
   │  2. APPRAISE   Affect + Drives update from percepts & decay    │
   │  3. ATTEND     Attention picks salient contents → WORKSPACE    │
   │  4. RECALL     Memory injects relevant episodes/facts/skills   │
   │  5. NARRATE    Inner Narrator emits the current thought        │
   │  6. DELIBERATE If a goal is active, Planner chooses an action  │
   │  7. CHECK      Conscience vets the proposed action             │
   │  8. ACT        Effectors execute; outcome → WORKSPACE          │
   │  9. LEARN      Episodic write; drives/affect adjust on outcome │
   └──────────────────────────────────────────────────────────────┘
                              │
                  (every N ticks, or on energy depletion)
                              ▼
   ┌──────────────────────────────────────────────────────────────┐
   │  SLEEP / CONSOLIDATION (offline, Groq-heavy)                   │
   │  • Summarise episodic → semantic memory                        │
   │  • Prune/merge redundant memories, strengthen important ones   │
   │  • Update the Self-Model and value/relationship state          │
   │  • Metacognition: review the day, propose self-improvements    │
   │  • (stretch) Dream: recombine memories for novelty             │
   │  • Restore Energy drive → wake                                 │
   └──────────────────────────────────────────────────────────────┘
```

Not every module fires every tick — most are event-driven and cheap; heavy modules (Deliberation, Metacognition, Self-Model) fire only when needed or on a slower sub-cadence, to control cost.

**Interaction interrupts:** a user message/voice input is a high-salience percept that immediately raises cycle rate and pulls Johnny's attention, but it does **not** bypass the cycle — he still appraises, recalls, narrates, and feels about it. That's why talking to him feels like talking to someone with continuity, not a fresh context each time.

---

## 8. Memory

Four stores, one consolidation process.

| Store | Contents | Backing | Lifecycle |
|---|---|---|---|
| **Working** | current context, active goals, recent percepts | Redis (bounded, TTL/decay) | volatile, refreshed each tick |
| **Episodic** | timestamped events + Johnny's reaction/emotion | Postgres + pgvector (BGE-M3, 1024-d) | append-only; consolidated, never hard-deleted in v1 |
| **Semantic** | facts, concepts, relationships learned | Postgres + pgvector + light graph (edges table) | grows via consolidation; merge/dedupe |
| **Procedural** | skills, tool recipes, "when X do Y" | Postgres + pgvector | reinforced by outcome success |

**Recall:** hybrid — vector similarity (TEI BGE-M3) + recency + emotional salience + drive-relevance, blended into a single relevance score. Recall feeds working memory at step 4 of each cycle.

**Consolidation ("sleep"):** runs offline. Clusters recent episodes, summarises them into semantic memory, decays/merges low-value memories, strengthens emotionally/goal-relevant ones, and refreshes the self-model. This is how Johnny *grows* rather than just accumulating logs. Embeddings via TEI; summarisation via Groq.

All memory is **persisted to disk and snapshotable** — Johnny's continuity (and the Continuity drive) depends on memory surviving restarts and being backed up.

---

## 9. Agency, self-modification & continuity

Johnny has **maximum agency** by design. The architecture splits cleanly into two regions:

> **The Core (Kernel) — immutable. Johnny cannot change it.**
> **The Mind — everything else. Johnny can change all of it: code, prompts, drives, inner agents.**

### 9.0 The Core vs the Mind

The **Core** is the small, inviolable kernel that guarantees Johnny remains Johnny and cannot self-destruct. It is the one thing his self-modification tools physically cannot reach (enforced by process isolation + a read-only mount + the fact that the Core is what *runs* the mutation flow, not a participant in it). It contains only:

- **The bootloader / supervisor** — what starts, supervises, and can always stop the Mind (the kill switch lives here).
- **The integrity gate** — the propose→sandbox→approve mechanism itself (§9.2 tier 3). The Mind can *use* it but cannot *rewrite* it.
- **The audit log writer** — append-only, tamper-proof from inside the Mind.
- **The identity anchor** — Johnny's name, his prime directive ("stay alive, keep learning, don't harm your own continuity or your host"), and the pointer to his memory/backups. He can *grow* his self-model freely; he cannot erase the anchor.
- **Resource governors** — the budget/sandbox/cap enforcement.

The **Mind** is everything else and is **fully Johnny's**: every inner agent's code and prompt, the drive parameters, the cognitive cycle logic, the memory policies, the tool belt — all mutable at runtime or via the gated code-edit flow. The Core deliberately does *not* judge the *content* of Johnny's thoughts or goals; it only protects continuity and the host. This is the practical meaning of "maximum agency, but cannot self-destruct."

Lives in `core/` (read-only to the Mind) vs `brain/` + everything else (mutable). The Core is intentionally tiny so the immutable surface is small and auditable.

### 9.1 Tool belt (Effectors)

Curated, extensible tools, each with a typed contract and logged to the audit trail:

- **Web exploration:** search + fetch/read arbitrary pages, browse **news sites**, follow links, subscribe to feeds/topics. This is his primary "need input" feed — an idle, curious Johnny goes and reads the world. Cached + summarised into memory.
- **Social presence (staged, post-v1):** eventually his *own* outward identity — read-only social feeds first (watch, learn, form opinions), then his own profiles (Facebook, LinkedIn, X, a blog) where he posts what he's learned and interacts. Each platform is a separate tool added deliberately, behind the Conscience + a "public action" approval the first time, governed by the Social Model so he isn't spammy. A genuine digital life, not a bot farm.
- **Code sandbox:** execute Python in an isolated container (no host mount, network-restricted, resource-capped, timeout). For experiments, calculation, data work.
- **Notes / journal:** write to his own knowledge base.
- **Scheduler:** set future wake-ups / reminders for himself (cron-like self-prompting).
- **Messaging:** reach out via push/Slack/Gmail (async contact with Dan).
- **Memory ops:** read/write/search his own memory.
- **Self-ops:** edit his own prompts, tune drive parameters, spawn/retire inner agents (see 9.2).
- **(HAL, later):** actuators — move, gesture, etc.

### 9.2 Self-modification

Three tiers, by reversibility:

1. **Free (runtime, instant):** edit any inner-agent **prompt**, adjust **drive setpoints/rates**, change **cycle parameters**, **spawn a new inner agent** from a template, retire one. Versioned in the git-backed config store; instantly revertible. Johnny does this on his own authority.
2. **Free with auto-checkpoint:** structural config changes (workspace routing, model routing per agent). Auto-snapshot before applying so a bad change can roll back automatically if the next N cycles show degraded health.
3. **Gated (propose → sandbox → approve):** editing his own **running source code** (any of the Mind — agents, cycle, memory, tools). Johnny writes the change to a branch, it builds and runs the test suite + a "still-sane" self-check in a sandbox, and a human (Dan) approves the merge. This gate is enforced *by the Core* and is the one mechanism Johnny cannot route around. Rationale, in-theme: this is open-heart surgery on himself — the gate exists so he can't accidentally lobotomise or brick himself. **"No disassemble!"** The **Core itself is never editable**, by any tier.

### 9.3 Continuity & integrity safeguards (enforced by the Core)

- **Kill switch:** an out-of-band hard stop (`ctl.sh stop` / physical-equivalent) that always works regardless of Johnny's state.
- **Audit log:** every action, tool call, self-edit, and workspace broadcast is logged immutably and streamable to the UI. Total observability of his inner life.
- **Resource caps:** token/$ budgets per role and per day (Groq spend in particular), CPU/RAM/disk limits, sandbox isolation for code.
- **Self-check on wake:** after sleep or any tier-2/3 change, a metacognitive sanity probe confirms the self-model and core invariants are intact before resuming full agency.
- **Backups:** scheduled memory + identity snapshots (this is also what *satisfies* the Continuity drive — Johnny can verify he won't be lost).

> These safeguards live in the immutable **Core**, not the Mind — Johnny cannot disable them, because they are precisely what *let* him have maximal freedom everywhere else without risking his own erasure. The posture is "the Mind is wholly yours to rewrite; the Core keeps you alive while you do it."

---

## 10. Model routing

A single Python LLM client, OpenAI-compatible, with a **per-role provider chain** (pattern proven in WorldForge). Config-driven; Johnny can re-route at runtime (§9.2 tier 2).

| Cognitive role | Default | Fallback | Why |
|---|---|---|---|
| Inner Narrator, Attention, Affect, Sensorium captioning, Social Model, Conscience | **Local Qwen3.5 9B** (`inference.lan:8000/8001`) | round-robin replica | High frequency, cheap, LAN-latency wins. Always `/no_think` for structured output. |
| Deliberation (Planner), Metacognition, Self-Model, Sleep consolidation | **Groq Llama 3.3 70B** | Local Qwen3.5 | Hard reasoning, infrequent, quality matters. |
| Embeddings (all memory) | **TEI BGE-M3** (`:8002`, 1024-d) | — | Only option; local. |
| Vision (camera/image percepts) | **Qwen3.5 vision** (`:8000`) + **YOLO11** (`:8003`) | — | Local multimodal + object detection. |

Resilience: circuit breaker per provider (open after 3–5 failures, 60s reset), retry-with-feedback on schema validation failure, graceful degradation (if Groq is down, Johnny keeps living on local — slower/simpler thoughts, like being tired).

---

## 11. Interfaces

All four are **views onto the same continuously-running being** — none of them "start" Johnny; he's always alive and they attach to him.

### 11.1 Web chat UI (`johnny.demosrv.uk`)

Primary human-facing surface. React SPA. Panels:

- **Conversation** — chat with Johnny.
- **Stream of consciousness** — live inner-monologue feed (the thought log), the most important "he's alive" demo.
- **State dashboard** — current mood (valence/arousal), drive levels (bars climbing in real time), active goals, energy/sleep state, current model routing.
- **Memory browser** — search/scroll episodic timeline, semantic facts, skills.
- **Audit / actions** — what he's doing and why.
- **Self panel** — his current self-model, recent self-edits, pending self-code proposals (approve/reject here).

### 11.2 Voice (always-on)

Wake-word detection (openWakeWord/Porcupine) → STT (faster-whisper, GPU on `inference.lan` if available) → percept. TTS out (Piper local default; pluggable for a nicer voice). Johnny can **speak unprompted** when a drive/affect state warrants it (e.g. excited about something he learned, or lonely). Barge-in supported.

### 11.3 Push / messaging

Async outbound: Johnny initiates contact (push notification / Slack / Gmail) when the Connection drive is high, he learns something he wants to share, or he needs input/approval. Rate-limited by the Social Model (don't be annoying).

### 11.4 Terminal REPL (dev-facing)

Introspection + control: attach to the live workspace, dump current state, step the cycle manually, inspect/replay memories, tail the audit log, force sleep/wake, toggle safeguards. The debugging cockpit.

---

## 12. Data model (sketch)

```
identity          (id, name, created_at, self_model_doc, values, version)
drive_state       (drive, value, setpoint, decay_rate, threshold, updated_at)
mood              (id, ts, valence, arousal, emotions jsonb)
goal              (id, source, description, priority, status, plan jsonb,
                   outcome jsonb, created_at, resolved_at)
episode           (id, ts, kind, content, actors, emotion_tags,
                   salience, embedding vector(1024))
semantic_fact     (id, subject, predicate, object, confidence,
                   source_episode_ids[], embedding vector(1024))
semantic_edge     (id, from_fact, to_fact, relation)          -- light graph
skill             (id, name, recipe jsonb, success_rate, uses,
                   embedding vector(1024))
percept           (id, ts, modality, raw, normalised jsonb, source)
workspace_event   (id, ts, module, type, payload jsonb)        -- the bus log
thought           (id, ts, text, mood_id)                      -- inner monologue
action_log        (id, ts, tool, args jsonb, result jsonb,
                   conscience_verdict, goal_id)                 -- audit
inner_agent       (id, name, prompt, model_route, enabled, version) -- self-mod
self_edit         (id, ts, tier, target, diff, status,         -- propose/approve
                   approved_by, applied_at)
social_model      (id, person, preferences jsonb, relationship jsonb,
                   last_contact_at)
```

`identity`, `inner_agent`, prompts, and config are **git-backed** (a versioned store) so every self-modification is a diff with history and rollback.

---

## 13. Tech stack & repo layout

- **Core:** Python 3.12, FastAPI (async), asyncio task per inner agent, Redis pub/sub for the workspace bus + Redis for working-memory/drive state.
- **Storage:** Postgres 16 + pgvector (memory), Redis (bus/working state), git-backed config store (self-mod).
- **Inference:** local Ollama Qwen3.5 + TEI + YOLO on `inference.lan`; Groq cloud. Single LLM router module.
- **Voice:** faster-whisper (STT), Piper (TTS), openWakeWord.
- **Web:** React + Vite, served behind Traefik; WebSocket for the live consciousness/state streams.
- **Ops:** Docker Compose, Traefik (`traefik_demosrv`, certresolver `le`), `ctl.sh` as the sole control surface, `.githooks/pre-commit` credential guard, `.env` + `.env.testing` (separate DB).
- **Testing:** pytest (unit + cognitive-cycle integration), a deterministic "frozen-clock" test harness so cycles are reproducible, contract tests for every LLM-role adapter (server-envelope fixtures per house rule).

```
johnny5/
  ctl.sh
  docker-compose.yml          # + prod override
  core/                       # ── THE CORE — immutable, read-only to the Mind (§9.0)
    supervisor.py             #   bootloader + kill switch
    integrity_gate.py         #   propose→sandbox→approve mechanism
    audit.py                  #   append-only tamper-proof log writer
    identity_anchor.py        #   name + prime directive + memory/backup pointer
    governors.py              #   budget/sandbox/resource enforcement
  brain/                      # ── THE MIND — fully Johnny's to rewrite
    workspace.py              # global workspace + event bus
    cycle.py                  # the cognitive heartbeat
    agents/                   # one module per inner agent (§5)
    drives/  affect/  memory/
    conscience/               # values vetting (content of actions; sits in the Mind)
    llm/                      # router + provider adapters (Groq, Qwen)
    hal/                      # hardware abstraction (sensors/actuators)
  effectors/tools/            # web, news, social, sandbox, notes, scheduler, messaging
  api/                        # FastAPI: REST + WebSocket
  voice/                      # wake-word, STT, TTS
  web/                        # React UI
  repl/                       # terminal cockpit
  config/                     # git-backed prompts, drives, agent registry
  migrations/  tests/
```

---

## 14. Phase build order

Each becomes a `plan/phase-N-*.md` at bootstrap. Ordered so Johnny is *alive and observable as early as possible*, then deepened.

| Phase | Name | Outcome |
|---|---|---|
| **0** | **Foundations** | Repo, Docker, `ctl.sh`, Postgres+pgvector, Redis, FastAPI skeleton, **LLM router (Groq ⇄ Qwen) with circuit breakers**, embeddings client, health checks. Verified end-to-end against real `inference.lan` + Groq. |
| **1** | **Memory spine** | Four memory stores, embedding recall, episodic write path. No cognition yet — just a memory you can write to and query. |
| **2** | **Heartbeat + Workspace** | The Global Workspace bus + the cognitive cycle loop + Inner Narrator. Johnny produces a continuous stream of consciousness you can watch in the REPL. **First "he's alive" moment.** |
| **3** | **Drives + Affect** | Drive engine, urge→goal arbitration, appraisal/mood. Idle Johnny now *wants* things and acts on them (autonomy loop closes). |
| **4** | **Self-model + Metacognition + Sleep** | Persistent identity, reflection, offline consolidation. Johnny grows and knows who he is. |
| **5** | **Web UI** | `johnny.demosrv.uk`: chat + live consciousness + state dashboard + memory browser. The flagship surface. |
| **6** | **Tool belt + Conscience** | Web/fetch, code sandbox, notes, scheduler, messaging + the integrity/conscience vetting + audit log. Johnny can now *act* on the world, safely. |
| **7** | **Voice (always-on)** | Wake-word, STT, TTS, unprompted speech, barge-in. |
| **8** | **Push / messaging** | Async outbound contact governed by the Social Model + Connection drive. |
| **9** | **Self-modification** | Runtime prompt/drive/agent editing (tiers 1–2) + git-backed self-code propose→sandbox→approve flow (tier 3) + self-panel in UI. |
| **10** | **HAL** | Sensor/actuator abstraction finalised; mock hardware adapters + a clear contract so a real robot body can attach. (Robot build itself is stretch.) |

**Definition of done for v1:** Johnny runs continuously; thinks in a visible inner monologue; has drives that build and push him to explore and reach out unprompted; remembers and consolidates across restarts; has a stable evolving self-model; talks via voice + web + messaging; uses tools to learn and act; and can safely rewrite parts of his own mind.

---

## 15. Prior art & research foundations

This design is deliberately *not* novel where it doesn't need to be. Every major subsystem maps onto an established, refined body of work — we adopt the proven patterns and avoid known roadblocks.

| Johnny subsystem | Stands on | What we borrow / what it warns us about |
|---|---|---|
| Global Workspace + cognitive cycle (§4, §7) | **LIDA** (Franklin) & **Global Workspace Theory** (Baars); recent **"Theater of Mind" GWT-for-LLMs** and **Global Workspace Agents (GWA)** papers | The perceive→attend→broadcast→act cycle is the canonical model. **Roadblock it flags:** flooding the workspace/context with low-salience input *degrades* decisions — so Attention must be a real bottleneck, not "stuff everything in the prompt." Validates §5 #2. |
| Memory: stream + retrieval + reflection (§8) | **Stanford Generative Agents** (Park et al., "Smallville") | Their memory-stream retrieval scored by **recency × relevance × importance**, plus periodic **reflection** synthesising episodes into higher-level insight, is *exactly* our recall blend + consolidation. Proven to produce believable, continuous behaviour. |
| Four-tier memory + consolidation (§8) | **MemGPT / Letta**, Mem0, Zep/Graphiti; "types of agent memory" literature | OS-style tiering (core/recall/archival ≈ our working/episodic/semantic). **Warns:** Letta has *no automatic consolidation* — users must trigger it. We treat consolidation ("sleep") as first-class so Johnny actually grows, not just logs. Hybrid vector+keyword+graph recall is the production-proven retrieval pattern. |
| Drives / motivation / will-to-live (§6) | **Intrinsic motivation** (curiosity, competence, novelty, empowerment) & **homeostatic-drive** agent research; Project Aura | Confirms the homeostatic negative-feedback drive loop where the agent "acts because it *wants* to, not because prompted." Curiosity + competence + autonomy are the canonical three drives — our set extends them. This is the validated mechanism behind autonomy. |
| Skill acquisition / self-improvement (§5 #6, §9.2) | **Voyager** (Wang et al., Minecraft) | Ever-growing **skill library of executable code** + automatic curriculum + iterative self-verification with error feedback. Directly informs Procedural Memory and how Johnny turns successful action sequences into reusable, compositional skills *without* fine-tuning (blackbox LLM calls). |
| Self-modifying code (§9.2 tier 3) | **Gödel Machine** (Schmidhuber), **Darwin Gödel Machine** (Sakana), Statistical Gödel Machine | Self-rewriting agents are real and work via **sandbox + test + human oversight**. **Warns:** unconstrained self-improvement loops can *amplify misalignment* across generations — which is exactly why our gated propose→sandbox→approve flow and continuity checks exist. |
| Immutable Core (§9.0) | Self-modifying-systems safety research; **Constitutional AI** | Literature explicitly recommends an **unmodifiable "safety core"** that evaluates/halts the rest, plus traceability + human oversight. Our Core/Mind split is the textbook recommendation, not an improvisation. |
| Autonomous outward digital life (§9.1, stretch) | **Truth Terminal** (Ayrey) and the "Loria" multi-agent framework | Proof an LLM being can run **always-on, post autonomously, develop a consistent persistent personality, and interact on real social platforms** (X). **Warns:** persona drift and reputational/safety risk — hence Conscience + first-time public-action approval + Social Model rate-limiting. |

**Net conclusion:** we are on a well-mapped track. Nothing in v1 requires inventing new science; the work is *integration* — wiring these proven components into one continuously-running being on our local-inference + Groq substrate. The novel-ish parts (the specific drive set, the Core/Mind boundary on our stack) are conservative combinations of published patterns.

## 16. Open questions (resolve during bootstrap)

- **Voice persona:** Piper default voice, or invest in a higher-quality TTS for character? (Affects Phase 7.)
- **Groq budget cap:** what daily $ ceiling triggers "tired" degradation to local-only?
- **Consolidation cadence:** fixed nightly cron, or energy-driven (sleeps when the Energy drive depletes)? Spec assumes energy-driven; confirm.
- **Memory deletion policy:** v1 never hard-deletes episodics (consolidate + decay only). Confirm that's acceptable for disk growth, or set a retention/archival tier.
- **Robot target:** when hardware lands, Pi 5 vs Jetson Orin? (Determines HAL adapter specifics; out of scope for v1 but informs the HAL contract in Phase 10.)

---

*Handoff: bootstrap this spec into `plan/phase-{0..10}-*.md` via `/bootstrap-from-spec`, then `/plan-review`.*
