# Lessons

### Run `ruff format --check` before committing teammate code
As the single-committer lead, a teammate reporting "ruff/mypy clean" usually means `ruff check` (lint) + `mypy`, NOT `ruff format`. They're separate gates and CI runs both. Phase 1 committed 5 `brain/memory/*.py` files that passed lint but failed `ruff format --check` (caught by qa, not at commit). Before each commit of teammate work, run `uv run ruff check . && uv run ruff format --check .` yourself — don't trust the "clean" report to cover formatting.

### Anchor runtime-state .gitignore patterns to the repo root
A bare `memory/` (for Johnny's runtime memory dir) silently matched `tests/memory/`, un-tracking the entire Phase 1 test suite. Root-anchor private runtime dirs as `/memory/`, `/data/`, `/snapshots/`, `/backups/` so they never collide with `tests/<name>/`.

### Reasoning-capable models need generous max_tokens for structured output — and a LIVE test to prove it
gemma4 (not just qwen) emits an internal reasoning chain before its answer when given a complex prompt + a JSON schema. With a tight `max_tokens` (e.g. 220) the reasoning consumes the entire budget → `content=""`, `finish_reason="length"` → no JSON → router schema-validation fails → (single-step provider chain) `LLMUnavailableError` → the feature silently produces nothing on every call. Set `max_tokens` with headroom for reasoning + the structured wrapper (the narrator needed ≈800–1024; reasoning ~250 + the JSON `{"thought": …}` fit comfortably), and make it a setting. CRUCIAL: deterministic/canned-router tests pass regardless of the token budget, so they CANNOT catch this — a `@pytest.mark.live` end-to-end test (real model, real budget) is required to cover the real-token path. Same family as the Phase-0 qwen empty-content artifact.

### Run DB/Redis-backed tests in-network (`./ctl.sh test`), not host pytest
Memory/DB tests reach Postgres/Redis at the compose hostnames `postgres`/`redis`, which only resolve inside the compose network. Host `uv run pytest` can't connect → ~25 connection ERRORS that look like failures but are purely environmental. The authoritative suite run is `./ctl.sh test` (in-network). Host pytest is fine for pure-unit/contract tests (no DB), and the lint/format gate runs anywhere. Same family as the Phase-0 live-health-leg (host-skipped, in-network-passes).

### Verify the live inference contract before writing clients (don't trust the spec doc)
The original SPEC assumed `qwen3.5:9b` + TEI-native embeddings + a `:8001` replica + Qwen vision. Reality (probed live): no such model tag, a custom `/embed` server, no replica, Qwen text-only / gemma4 multimodal, and Ollama had silently fallen back to CPU. Probe `inference.lan` + capture real response envelopes as fixtures before building adapters — contract tests fed by captured wire shapes catch doc drift (qwen returns `content`+`reasoning`, not empty content as first documented).
