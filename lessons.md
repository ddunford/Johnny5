# Lessons

### Run `ruff format --check` before committing teammate code
As the single-committer lead, a teammate reporting "ruff/mypy clean" usually means `ruff check` (lint) + `mypy`, NOT `ruff format`. They're separate gates and CI runs both. Phase 1 committed 5 `brain/memory/*.py` files that passed lint but failed `ruff format --check` (caught by qa, not at commit). Before each commit of teammate work, run `uv run ruff check . && uv run ruff format --check .` yourself — don't trust the "clean" report to cover formatting.

### Anchor runtime-state .gitignore patterns to the repo root
A bare `memory/` (for Johnny's runtime memory dir) silently matched `tests/memory/`, un-tracking the entire Phase 1 test suite. Root-anchor private runtime dirs as `/memory/`, `/data/`, `/snapshots/`, `/backups/` so they never collide with `tests/<name>/`.

### Verify the live inference contract before writing clients (don't trust the spec doc)
The original SPEC assumed `qwen3.5:9b` + TEI-native embeddings + a `:8001` replica + Qwen vision. Reality (probed live): no such model tag, a custom `/embed` server, no replica, Qwen text-only / gemma4 multimodal, and Ollama had silently fallen back to CPU. Probe `inference.lan` + capture real response envelopes as fixtures before building adapters — contract tests fed by captured wire shapes catch doc drift (qwen returns `content`+`reasoning`, not empty content as first documented).
