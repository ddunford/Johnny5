# Johnny's voice

A synthetic voice for Johnny 5: a base TTS voice (Kokoro on `inference.lan:8880`)
plus a small DSP character chain (`johnnify.py`).

## Use

```bash
# speak a line (Kokoro -> johnnify)
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
2. **ring modulation** — metallic/electronic timbre
3. **comb filter** — short mechanical "servo" resonance
4. **bitcrush** — light digital grit (amplitude quantise + sample-hold)
5. **chorus** — detuned delay → stacked/synthetic feel
6. **normalize**

Dependencies: `numpy` + stdlib `wave` only.

## Notes

- **Kokoro is a CPU container** — ~55 s to synthesise a short line. Voice must be
  streamed/queued in Phase 7 and must never block the cognitive cycle.
- Kokoro's WAV header reports an unreliable frame-count (streamed WAV); `johnnify`
  reads the actual byte payload, so it's unaffected. Downstream tools should do the same.
- Base voice is currently a Kokoro preset (`am_puck`/`am_echo`/`am_fenrir`),
  optionally blended.

## Status

Proof-of-concept for **Phase 7 (voice)**. Audition samples write to
`.artifacts/voice/` (gitignored) — play locally, e.g. `aplay voice/samples/am_puck_johnny.wav`.
