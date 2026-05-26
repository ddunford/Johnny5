# Johnny's voice

An **original** synthetic-robot voice for Johnny 5 — a base TTS voice (Kokoro on
`inference.lan:8880`) plus a small DSP "robot character" chain (`johnnify.py`).

> **Why not clone the film?** Short Circuit's Johnny 5 is actor Tim Blaney's live
> performance, not a synth. Cloning a real actor's copyrighted voice into a public
> repo is an IP/likeness risk. We instead build the *character* — eager, metallic,
> lightly digital — as our own voice. Public-repo safe, fully controllable, no
> model training, no new GPU tenant.

## Use

```bash
# speak a line in Johnny's voice (Kokoro -> johnnify)
./say.sh "Johnny Five is alive!" johnny.wav

# tune it
KOKORO_VOICE=am_echo JOHNNY_PRESET=heavy ./say.sh "I need input." out.wav

# or post-process an existing 16-bit WAV directly
python3 johnnify.py in.wav out.wav --preset johnny
```

Presets (in `johnnify.py`): `subtle` · `johnny` (default) · `heavy` — increasing
pitch-up, ring-mod, comb resonance, bitcrush, and chorus.

## The chain (`johnnify.py`, numpy only)

1. **resample** — mild pitch-up → youthful, eager read
2. **ring modulation** — the metallic/electronic timbre
3. **comb filter** — short mechanical "servo" resonance
4. **bitcrush** — light digital grit (amplitude quantise + sample-hold)
5. **chorus** — detuned delay → stacked/synthetic feel
6. **normalize**

Dependencies: `numpy` + stdlib `wave` only. (ffmpeg on `inference.lan` is an
alternative engine if we ever want richer filters.)

## Notes / caveats

- **Kokoro is a CPU container** — ~55 s to synthesise a short line. Voice must be
  streamed/queued in Phase 7 and must never block the cognitive cycle.
- Kokoro's WAV header reports an unreliable frame-count (streamed WAV); `johnnify`
  reads the actual byte payload, so it's unaffected. Downstream tools should do the same.
- Base voice is currently Kokoro presets (`am_puck`/`am_echo`/`am_fenrir` are good
  Johnny candidates) optionally blended. If we later want a *bespoke* base timbre,
  the Phase 7 plan covers a few-shot clone (Chatterbox/F5-TTS) of a voice we own —
  then this same DSP chain finishes it.

## Status

Proof-of-concept. Belongs to **Phase 7 (voice)**. Audition samples are written to
`.artifacts/voice/` (gitignored) — play locally, e.g. `aplay .artifacts/voice/am_puck_johnny.wav`.
