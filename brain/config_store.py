"""The git-backed config store — externalised prompts (FC-3).

Anything Johnny will later edit at runtime (agent prompts, drive params, the agent
registry) is config, not a code constant, from the start. This module resolves an
inner agent's prompt by name with a two-layer lookup:

* ``config/runtime/prompts/{name}.md`` — the live, Johnny-editable copy
  (``config/runtime`` is gitignored; Phase 9 self-modification writes here).
* ``config/prompts/{name}.md`` — the versioned default committed to the repo.

The runtime override wins when present, so a self-edit shadows the default without
touching code — and reverting is just deleting the runtime file. This mirrors how
``LLM_ROUTES_PATH`` already lets a runtime copy override the committed routing
policy. A missing prompt is a configuration error worth surfacing loudly rather
than silently degrading cognition.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_PROMPTS_SUBDIR = "prompts"


class PromptNotFoundError(FileNotFoundError):
    """No prompt file (runtime or default) exists for the requested agent."""


class ConfigStore:
    """Resolves versioned/runtime config files (prompts for now)."""

    def __init__(
        self,
        *,
        base_dir: str | Path = "config",
        runtime_dir: str | Path | None = None,
    ) -> None:
        self._base = Path(base_dir)
        # Defaults to ``{base}/runtime`` (the gitignored live layer) unless
        # pointed elsewhere (tests use a tmp dir).
        self._runtime = Path(runtime_dir) if runtime_dir is not None else self._base / "runtime"

    def prompt_path(self, name: str) -> Path:
        """The path the prompt would resolve from (runtime override beats default)."""
        override = self._runtime / _PROMPTS_SUBDIR / f"{name}.md"
        if override.is_file():
            return override
        return self._base / _PROMPTS_SUBDIR / f"{name}.md"

    def load_prompt(self, name: str) -> str:
        """Return an agent's prompt text, runtime override winning over default."""
        path = self.prompt_path(name)
        if not path.is_file():
            raise PromptNotFoundError(
                f"no prompt for agent {name!r}: looked in "
                f"{self._runtime / _PROMPTS_SUBDIR} then {self._base / _PROMPTS_SUBDIR}"
            )
        return path.read_text(encoding="utf-8").strip()


@lru_cache(maxsize=1)
def get_config_store() -> ConfigStore:
    """The process-wide config store over the repo's ``config/`` directory."""
    return ConfigStore()
