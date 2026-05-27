"""Load and resolve the per-role routing policy from ``config/llm_routes.toml``.

The TOML binds logical model *slots* to a provider + a settings attribute (so
model ids stay single-sourced in ``.env``), and maps each cognitive role to an
ordered chain of slots. This module resolves that into concrete
``(provider, model)`` steps the router can execute.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic import BaseModel

from foundation.config import Settings


class ModelStep(BaseModel):
    """One concrete step in a role's chain: a provider and the model to call."""

    provider: str
    model: str


class RoutingConfig(BaseModel):
    """Resolved routing policy: role name → ordered list of model steps."""

    roles: dict[str, list[ModelStep]]

    def chain_for(self, role: str) -> list[ModelStep]:
        """Return the chain for a role, falling back to ``default``.

        An unknown role with no ``default`` configured is a config error worth
        surfacing loudly rather than silently dropping cognition.
        """
        if role in self.roles:
            return self.roles[role]
        if "default" in self.roles:
            return self.roles["default"]
        raise KeyError(f"no route for role {role!r} and no 'default' configured")


def load_routing_config(settings: Settings, path: str | Path | None = None) -> RoutingConfig:
    """Parse the routing TOML and resolve model slots against settings."""
    routes_path = Path(path) if path is not None else Path(settings.llm_routes_path)
    with routes_path.open("rb") as fh:
        raw = tomllib.load(fh)

    slots = raw.get("models", {})
    resolved_slots: dict[str, ModelStep] = {}
    for slot_name, slot in slots.items():
        provider = slot["provider"]
        model_setting = slot["model_setting"]
        if not hasattr(settings, model_setting):
            raise ValueError(
                f"model slot {slot_name!r} references unknown settings field {model_setting!r}"
            )
        model = str(getattr(settings, model_setting))
        resolved_slots[slot_name] = ModelStep(provider=provider, model=model)

    roles: dict[str, list[ModelStep]] = {}
    for role, slot_names in raw.get("roles", {}).items():
        steps: list[ModelStep] = []
        for slot_name in slot_names:
            if slot_name not in resolved_slots:
                raise ValueError(f"role {role!r} references unknown model slot {slot_name!r}")
            steps.append(resolved_slots[slot_name])
        roles[role] = steps

    if not roles:
        raise ValueError(f"no roles defined in routing config at {routes_path}")

    return RoutingConfig(roles=roles)
