#!/usr/bin/env bash
# Speak a line in Johnny's voice: Kokoro TTS (base) -> johnnify DSP (robot character).
#   ./say.sh "text to speak" [out.wav]
# Env: TTS_BASE_URL, KOKORO_VOICE (default am_puck), JOHNNY_PRESET (subtle|johnny|heavy)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
TEXT="${1:-Johnny Five is alive!}"
OUT="${2:-johnny.wav}"
VOICE="${KOKORO_VOICE:-am_puck}"
PRESET="${JOHNNY_PRESET:-johnny}"
TTS_BASE="${TTS_BASE_URL:-http://inference.lan:8880}"

tmp="$(mktemp --suffix=.wav)"
trap 'rm -f "$tmp"' EXIT
# JSON-encode the text safely
payload="$(python3 -c 'import json,sys; print(json.dumps({"model":"kokoro","voice":sys.argv[1],"input":sys.argv[2],"response_format":"wav"}))' "$VOICE" "$TEXT")"
curl -s -m180 "$TTS_BASE/v1/audio/speech" -H 'Content-Type: application/json' -d "$payload" -o "$tmp"
python3 "$HERE/johnnify.py" "$tmp" "$OUT" --preset "$PRESET"
echo "spoke: \"$TEXT\"  ->  $OUT  (voice=$VOICE preset=$PRESET)"
