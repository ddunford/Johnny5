# Phase 7: Voice (always-on)

## Overview
Johnny gains an always-on voice channel: he hears the wake word, has the spoken request transcribed into a percept, thinks about it through the normal cognitive cycle, and speaks back **in his own voice** — and crucially, **speaks unprompted** when his drives (esp. Connection / "I want to share") push him to. The whole point of this phase, per `SPEC §9.1`: "you can talk to him out loud, and he talks back unprompted, in his own voice."

Built on the existing substrate — there is no new cognitive agent here. Speech-in is just another percept source on the `Sensorium`'s `InputQueue` (it mirrors `POST /api/v1/input`); speech-out is a vetted `speak` **Tool** on 6a's `EffectorDispatch` (`danger:public` — it's externally visible, hard to unsay). The `Conscience` already vets every tool action ("should I say this, given my values?") and the durable `action_log` records every spoken line — the safety story is inherited from 6a, not re-invented.

The defining architectural constraint: **STT and TTS are CPU-bound and slow** (faster-whisper-small + Kokoro ≈ seconds, not ms). The cognitive heartbeat MUST NEVER block on them. Speech I/O lives in a **separate `audio_daemon` sidecar** that owns `/dev/snd`, runs openWakeWord continuously, calls Speaches/Kokoro, drives the existing `voice/` robot-voice DSP, and communicates with the api via the existing `InputQueue` (speech-in) + a Redis pub/sub channel (speech-out + voice-activity events). The cycle dispatches a `speak` and returns; the daemon synthesises + plays asynchronously.

**Done when:** the wake word fires → audio is transcribed → a percept lands on `/api/v1/input` → the cycle thinks → Deliberation may propose a `speak(text)` action → the Conscience vets it → the audio daemon TTSes through the robot-voice DSP → speakers; he ALSO speaks unprompted when Connection / sharing drives push him; **barge-in works** (the wake word interrupts current playback and starts listening); the heartbeat keeps ticking at its target interval throughout (proven by a latency test). All speech is `action_log`'d.

## Forward-commitment touchpoints
- **FC-5 — speech-out is a Tool through the one audited dispatch.** A `speak` Tool joins 6a's `ToolRegistry`; every spoken line goes through `EffectorDispatch.vet→commit→audit`. No back-door audio path. Speech-in is just a percept on the existing `InputQueue` (no new dispatch).
- **FC-7 — the cycle pipeline shape doesn't change.** Wake/STT happens *outside* the cycle (audio daemon → InputQueue → PERCEIVE). The cycle's CHECK + ACT do exactly what they do for any other tool. No new tick stage.
- **FC-9 — the Conscience vets speech, fully Johnny-editable.** "Should I say this, given my values?" is values, not host-safety. NO un-loosenable speech-content floor in code/core. (Host-safety for voice is trivially the same as any tool: budget gate, audit, kill-switch — none of which judge content.)
- **FC-4 — STT/TTS are local + free, do NOT go through `LLMRouter`.** They're separate inference services with their own clients; no budget gate (CPU-local). The router-gated LLM call is the cycle's *cognition* (already gated), not the TTS rendering.
- **FC-8 — voice activity is a state surface.** "Listening / thinking / speaking / idle" rides the existing state stream so the UI has a live indicator without a second WS channel.
- **`speak` is `danger:public`.** First public-class tool (`SPEC §9` staging — outward presence stays post-v1, but speaking aloud IS outward). The Conscience's prompt should reflect "this leaves my mouth — others hear it" weight (a values consideration, still fully editable).

## Custom Feature: the voice channel

**Database tables:** none. Speech-in surfaces as `percept` (existing table); speech-out as a `note`-style line if Johnny wants to keep it, otherwise it lives only in `action_log` (the tool, args=text, result=spoken-ts). No new table for "spoken lines" — `action_log` IS the speech record.

**Internal interfaces:**
- **`audio_daemon`** (new sidecar container under `ops/audio/`, joins `--device /dev/snd` or PulseAudio socket): a single Python service that owns the mic + speakers and stays running. Three loops:
  - **listen loop** — continuous mic capture → openWakeWord; on wake → record until VAD silence → Speaches STT (`inference.lan:8890`, `Systran/faster-whisper-small`) → `POST /api/v1/input` with `source=voice` so the percept is tagged.
  - **speak loop** — subscribes to a Redis channel `johnny:speak`; payload `{text, action_id}` → Kokoro TTS (`inference.lan:8880`) → `voice/` robot-voice DSP (the PoC) → audio output device. Marks `action_id` complete on done.
  - **state loop** — emits `voice.state` events (`listening` / `transcribing` / `speaking` / `idle`) to Redis `johnny:voice` so the cycle/UI can surface activity.
- **`SpeakTool`** (new, `brain/effectors/speak.py`, `danger:public`): args = `{text: str (max 500 chars)}`; pushes `{text, action_id}` to `johnny:speak` and returns a `ToolResult` IMMEDIATELY (fire-and-forget — the cycle doesn't wait for audio). The daemon's "speak done" Redis event is logged but doesn't block the action — the dispatch's `action_log` row captures intent + the daemon-completion adds a follow-up bus event.
- **`Sensorium`** gains a `source=voice` tag on percepts arriving via `POST /api/v1/input` (zero new endpoint — it's the existing input path with a metadata field).
- **`Deliberation`** (extended): a new drive→tool mapping `Connection → speak` (when he wants to share / connect, formulate a one-line `speak` proposal via an LLM step, like Mastery→code_exec in 6b). Internal "narrator" thoughts remain on the bus + are NOT auto-spoken; only deliberate `speak` proposals reach his mouth.
- **Barge-in** is owned by the audio daemon, not the cycle: on wake-word detection during playback, the daemon CANCELS the current playback locally + transitions to `listening`, no signal back to the cycle needed (the next percept just arrives as normal).
- **Voice-activity state** flows through the existing `state` surface — `voice.state` published by the daemon → consumed by the cycle's state composer → added to the `/api/v1/state` + `/ws/state` payloads.

**Tools added to 6a's registry:**
| Tool | danger | Backed by | Notes |
|------|--------|-----------|-------|
| `speak` | **public** | Kokoro + DSP + speakers (via the audio daemon) | Conscience-vetted, audited; fire-and-forget for the cycle; max-len capped to keep the panel/cost bounded |

**Key patterns (non-obvious):**
- **The cognitive cycle MUST NOT await STT or TTS.** STT runs in the daemon, before a percept exists. TTS runs in the daemon, after the cycle has dispatched. The cycle's hottest tick is unchanged.
- **Speech-in is "just" a percept.** No special routing, no separate dispatch — the existing `POST /api/v1/input` is the entry, with `source=voice`. This is what lets cognition treat a spoken question identically to a typed one (the simplest correct design).
- **Internal cognition is NOT speech.** Reflect/recall/narrate produce thoughts on the bus; only `speak` actions reach the mouth. This preserves the 6a guardrail ("if it acts on the world, it's a Tool"). A future "Voice" prompt could choose what's worth saying from the thought stream, but P7 stays explicit: Deliberation proposes `speak`, never auto-narrates aloud.
- **Wake-word is the consent gate.** Johnny doesn't transcribe ambient audio — only between wake and VAD-silence. This is also why STT cost isn't a concern (only bursts, not continuous).
- **The Conscience's prompt should know `danger:public` is unsay-able.** The vet for `speak` actions should weigh "leaves my mouth" more heavily than `danger:safe`. (No code floor — just the values prompt.)
- **Audio daemon is the trust boundary for the mic.** A malicious adversarial-audio attack (spoken prompts trying to manipulate the cycle) lands as a normal percept → cognition → Conscience vets any resulting `speak`/tool action against Johnny's values. There's no special handling because there shouldn't be: the same vet that handles a fetched-page injection (LOW backlog from 6b) handles a spoken-prompt injection.

**Test checklist:** see `test-plan-phase-7.md`.

## Implementation steps
1. `audio_daemon` skeleton: sidecar container (Dockerfile under `ops/audio/`), `--device /dev/snd` (or PulseAudio socket), Python service, the three loops as stubs, Redis pub/sub channels (`johnny:speak`, `johnny:voice`).
2. Wake-word: integrate openWakeWord; fires `wake` events to the listen loop.
3. STT client (Speaches `inference.lan:8890`): VERIFY THE CONTRACT LIVE first (lessons.md: verify external access before writing clients); audio → transcript. Listen loop posts to `/api/v1/input` with `source=voice`.
4. TTS client (Kokoro `inference.lan:8880`): VERIFY the contract live; text → audio bytes.
5. Robot-voice DSP: integrate the existing `voice/` PoC into the speak loop (TTS audio → DSP → audio out).
6. `SpeakTool` (`brain/effectors/speak.py`, `danger:public`): pushes to Redis, fire-and-forget; registered in 6a's `ToolRegistry`. Conscience-vetted via the existing dispatch.
7. Deliberation extension: `Connection → speak` mapping (LLM step formulates the spoken line, like Mastery→code_exec); Mastery/Curiosity etc. unaffected.
8. Barge-in: daemon-local — wake-word during playback cancels TTS + transitions to listening.
9. Voice-activity state: `voice.state` published by daemon → composed into `/api/v1/state` + `/ws/state`. UI panel/indicator gains a "🎤 listening / 💬 speaking" surface.
10. Latency guard: a non-blocking test that proves the heartbeat ticks at its target rate while a TTS run is in flight.
11. Tests + security review (see below).

## Tasks
- [ ] `TASK-7.0` Live-contract verification for **Speaches STT** + **Kokoro TTS** on `inference.lan:8890`/`:8880` BEFORE writing clients (lessons.md): real audio → real transcript shape; real text → real audio shape; capture both as `tests/fixtures/audio/` reference samples. **Do first.** → `/devops-deployment-engineer` + `/fastapi-engineer` [TC-7.2, TC-7.3]
- [ ] `TASK-7.1` `audio_daemon` skeleton (sidecar container, `--device /dev/snd` or PulseAudio, Python service, three loops as stubs, Redis pub/sub channels `johnny:speak` + `johnny:voice`); `ctl.sh` builds + runs it → `/devops-deployment-engineer` [TC-7.1]
- [ ] `TASK-7.2` Wake-word: openWakeWord integration in the listen loop; on wake → emit `wake` event + start recording → VAD silence → trigger STT → POST `/api/v1/input` with `source=voice` → percept lands on the `InputQueue` → cycle PERCEIVE picks it up. → `/fastapi-engineer` [TC-7.1, TC-7.4]
- [ ] `TASK-7.3` STT client (Speaches `inference.lan:8890`, `faster-whisper-small`), built against the verified live contract from 7.0; graceful degradation on STT-down (no crash, drop the wake). → `/fastapi-engineer` [TC-7.2]
- [ ] `TASK-7.4` TTS client (Kokoro `inference.lan:8880`), built against the verified live contract from 7.0; graceful degradation on TTS-down (`speak` returns `success=False`, audited, no audio). → `/fastapi-engineer` [TC-7.3]
- [ ] `TASK-7.5` Integrate the existing `voice/` robot-voice DSP into the speak loop (TTS bytes → DSP → audio out via the device). The audio runtime (alsa/pulseaudio + ffmpeg) ships with the daemon container from `TASK-7.1`, so this is pure Python wiring — the speak loop pipes Kokoro bytes through the PoC's DSP chain and emits to the configured output device. → `/fastapi-engineer` [TC-7.5]
- [ ] `TASK-7.6` `SpeakTool` (`brain/effectors/speak.py`, **`danger:public`**, max-len capped): pushes `{text, action_id}` to `johnny:speak`, returns `ToolResult` immediately (fire-and-forget, so the cycle never waits for audio); registered into 6a's `ToolRegistry` via `belt.build_tool_registry`. The daemon emits a `speak.done` Redis event the cycle logs as a follow-up bus event (not gating the `action_log` row). → `/fastapi-engineer` [TC-7.6]
- [ ] `TASK-7.7` Update `config/prompts/conscience.md` to weigh `danger:public` (speech) heavier — "this leaves my mouth, others hear it, it's hard to take back" — as a values consideration, NOT a code floor (FC-9). → `/fastapi-engineer` [TC-7.7]
- [ ] `TASK-7.8` Deliberation extension: `Connection → speak` mapping (an LLM step formulates the spoken line from goal+workspace, mirroring Mastery→code_exec); tired/no-router falls back to internal action (never proposes empty speech). One per tick. → `/fastapi-engineer` [TC-7.7, TC-7.8]
- [ ] `TASK-7.9` Barge-in (daemon-local): on wake-word detection during `speaking`, cancel current playback + transition to `listening` (no cycle signal needed — the next percept arrives normally). → `/fastapi-engineer` [TC-7.9]
- [ ] `TASK-7.10` Voice-activity state surface: daemon publishes `voice.state` (`idle`/`listening`/`transcribing`/`speaking`) to Redis `johnny:voice` → cycle's state composer adds it to `/api/v1/state` + `/ws/state` payloads; the durable shape is captured into `tests/fixtures/wire/state.json` on re-capture. → `/fastapi-engineer` [TC-7.11]
- [ ] `TASK-7.11` Frontend: a voice-activity indicator (🎤 listening / 💬 speaking / 🧠 thinking / idle) on the dashboard (subscribes to `/ws/state`); pinned with a captured-wire contract test. → `/frontend-react-architect` [TC-7.11]
- [ ] `TASK-7.12` Latency guard (the load-bearing architectural test): with the speak loop deliberately stalled (slow TTS stub), the cycle's heartbeat ticks at its target interval and other agents (drives, narrator, deliberation) keep running. Proves speech I/O never blocks cognition. → `/qa-test-engineer` [TC-7.10]
- [ ] `TASK-7.13` ⫘ Tests: per-component (wake/STT/TTS/DSP/speak-tool deterministic + arg-validation + Conscience-vet + fire-and-forget); the **voice loop E2E** (wake → percept → cycle responds → Deliberation proposes a `speak` → Conscience allow → audio queued → daemon plays); barge-in; latency guard; voice-activity state in `/ws/state`; full suite 3× no-regression. Frozen clock + stub Speaches/Kokoro + stub audio device. → `/qa-test-engineer` [TC-7.1..7.12]
- [ ] `TASK-7.14` ⫘ Contract + `@live`: `parse_stt_response` + `parse_tts_response` captured-envelope projections; `@live` legs against real Speaches + real Kokoro at `inference.lan` (one of each, like 6b's @live SearXNG); the AuditPanel/voice-state contract pinned against captured wire. → `/qa-test-engineer` [TC-7.2, TC-7.3, TC-7.11]
- [ ] `TASK-7.15` ⫘ Security review (lead-handled): the `speak` tool is `danger:public` (first one) — verify (a) the Conscience prompt reflects the unsay-able weight, (b) the Conscience CAN veto on values (a permissive prompt allows; FC-9 preserved), (c) audio-input adversarial / spoken prompt-injection is handled by the SAME vet path as fetched-content (LOW from 6b — Conscience is the boundary, no new code path needed); (d) the audio daemon runs non-root + has no project-code mount; (e) the Redis `johnny:speak` channel can't be written by anything outside the api (Redis ACL or compose-network scoping); (f) Speaches/Kokoro are trusted infra (like SearXNG — internal endpoint, no SSRF gate needed). → `/security-reviewer` (lead) [TC-7.7, TC-7.9, TC-7.13]

## Notes
- **Voice is dev-side first.** The audio daemon runs on whatever box has the mic/speakers (the dev box for now; the robot host for P10). On `munero01` prod there's no mic — the daemon simply isn't deployed there. The api runs without the daemon and `speak` becomes a no-op tool that audits the intent but skips the audio publish (graceful, not an error — the Conscience can still vet). Wire that explicit "no daemon → audit-only" branch.
- **Out of scope:** speaker recognition / "who's talking" (only Dan speaks for v1 — every voice percept is treated as Dan); streaming partial transcripts (final-transcript only is the MVP); multi-turn dialogue management (the cycle's normal cognition handles conversation — no separate dialogue agent); outward voice on the web UI (no in-browser speech-out — that's a different latency budget). Push/Slack messaging is P8; self-modification of the voice prompt is P9; physical voice on a robot body is P10.
- **`SPEC §10` "always /no_think for the reasoning model"** is already on (6a.7); the Mastery/Connection speech-formulation LLM step uses the reasoning role + `/no_think` automatically.
- **The first time he speaks unprompted is the demo.** Open the UI, say nothing; when Connection drifts high (he wants to share), he speaks. That's the phase landing.

## Carried-over advisories
- The **2 LOW** security residuals from 6b (Conscience injection-resistance prompt framing; novel-secret redaction) remain in the security-hardening backlog. P7 doesn't change them: spoken adversarial input rides the same percept/vet path as a fetched page, so the same protections (the Conscience's values + the absence of fetched/spoken content in a vetted action's `args`) apply. Re-evaluate when P8 (outward messaging) approaches.
- The **`audio_daemon` is the first non-api host-resource container** (it owns `/dev/snd`). Document its setup in `~/.claude/local/inference-services.md` (or a sibling `local/audio.md`) so future setups know about the device passthrough + the PulseAudio fallback path.
