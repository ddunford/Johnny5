"""The Self-Model agent — anchor-grounded reflection into a new identity version.

Each sleep, ``SelfModel.refresh(inputs)`` reflects on Johnny's recent experience,
consolidated knowledge, and mood/drive state — against the **immutable Core anchor**
(read-only, FC-1) and his current self-model — and writes the next ``identity``
version through the ``self_model`` router role. The anchor is the trusted reference:
the refreshed self-model grows *around* it (the name is always taken from the
anchor, so the self-model can never rename Johnny), and the wake self-check later
trips if the doc drifts off it.

``parse_identity_delta`` is the pure projection (model JSON → typed delta) the
contract test feeds a captured envelope through (FC-4 house rule). When every
provider is tired the refresh degrades to **keeping the current self-model**
(no new version, logged) rather than fabricating one — the sleep pipeline records
the degraded stage and still wakes (per-stage isolation).
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, Field

from brain.config_store import ConfigStore, PromptNotFoundError, get_config_store
from brain.llm.base import LLMUnavailableError, Message
from brain.llm.router import LLMRouter
from brain.self_model.store import IdentityDoc, IdentityStore
from core.identity_anchor import IdentityAnchor, load_identity_anchor
from foundation.config import get_settings
from foundation.observability import get_logger

_log = get_logger("brain.self_model")

SELF_MODEL_AGENT_NAME = "self_model"
SELF_MODEL_ROLE = "self_model"

# Reflective but grounded — a moderate temperature.
_TEMPERATURE = 0.6


# ── inputs + the model response contract (pure projection) ─────────────────────


class ReflectionInputs(BaseModel):
    """What the self-model reflection considers, gathered by the sleep orchestrator.

    Plain strings so the agent stays decoupled from the memory/affect stores and is
    trivially testable — the orchestrator (sleep cycle) builds these from the recent
    episodes, the freshly-consolidated facts, and the current mood/drive readings.
    """

    recent_episodes: list[str] = Field(default_factory=list)
    semantic_facts: list[str] = Field(default_factory=list)
    mood: str = ""
    drives: str = ""


class IdentityDelta(BaseModel):
    """The refreshed self-concept the ``self_model`` role returns.

    Note there is no ``name`` field by design — the name is the immutable anchor's,
    not the model's to change (FC-1). The agent stamps it from the anchor.
    """

    self_model_doc: str
    values: list[str] = Field(default_factory=list)
    concerns: list[str] = Field(default_factory=list)
    relationships: dict[str, str] = Field(default_factory=dict)


def parse_identity_delta(content: str) -> IdentityDelta:
    """Project a self-model model response (JSON) into an ``IdentityDelta`` (pure).

    The house-rule contract seam (TASK-4.13): a captured model envelope fed through
    here asserts the projection, so an output-shape change surfaces here, not
    silently as a corrupted self-model.
    """
    return IdentityDelta.model_validate_json(content)


# ── the agent ────────────────────────────────────────────────────────────────


class SelfModel:
    """Refreshes Johnny's versioned self-model from anchor-grounded reflection."""

    name = SELF_MODEL_AGENT_NAME
    model_route = SELF_MODEL_ROLE

    def __init__(
        self,
        router: LLMRouter | None = None,
        *,
        store: IdentityStore | None = None,
        config_store: ConfigStore | None = None,
        anchor: IdentityAnchor | None = None,
        max_tokens: int | None = None,
    ) -> None:
        settings = get_settings()
        self._router = router
        self._store = store or IdentityStore()
        # Read the Core anchor once (read-only — never mutated, FC-1).
        self._anchor = anchor or load_identity_anchor()
        self.prompt = self._load_prompt(config_store)
        self._max_tokens = max_tokens if max_tokens is not None else settings.self_model_max_tokens

    @staticmethod
    def _load_prompt(config_store: ConfigStore | None) -> str:
        try:
            return (config_store or get_config_store()).load_prompt(SELF_MODEL_AGENT_NAME)
        except PromptNotFoundError:
            return ""

    async def bootstrap(self) -> IdentityDoc:
        """Idempotently ensure the v1 self-model exists; return the current one.

        The ``DriveEngine.bootstrap`` analogue — safe on every startup, and the seam
        a truncating test calls to re-establish the anchor-grounded v1 baseline.
        """
        return await self._store.ensure_seeded()

    async def current(self) -> IdentityDoc:
        """The current self-model, seeding v1 from the anchor if none exists yet."""
        return await self._store.ensure_seeded()

    async def refresh(self, inputs: ReflectionInputs) -> IdentityDoc:
        """Reflect on ``inputs`` and persist the next self-model version.

        On success a new version (``current + 1``) is written and returned. When the
        model is unavailable (no router, no prompt, or every provider tired) the
        refresh degrades to returning the current self-model unchanged (no new
        version) — the sleep stage records the degradation and still wakes.
        """
        current = await self._store.ensure_seeded()
        delta = await self._reflect(current, inputs)
        if delta is None:
            return current
        return await self._store.append(
            IdentityDoc(
                name=self._anchor.name,  # immutable — taken from the anchor (FC-1)
                self_model_doc=delta.self_model_doc,
                values=delta.values,
                concerns=delta.concerns,
                relationships=delta.relationships,
            )
        )

    async def _reflect(
        self, current: IdentityDoc, inputs: ReflectionInputs
    ) -> IdentityDelta | None:
        """Produce a refreshed self-concept via the ``self_model`` role; None when tired."""
        if self._router is None or not self.prompt:
            return None
        messages = [
            Message(role="system", content=self.prompt),
            Message(role="user", content=self._render(current, inputs)),
        ]
        try:
            completion = await self._router.complete(
                SELF_MODEL_ROLE,
                messages,
                schema=IdentityDelta,
                temperature=_TEMPERATURE,
                max_tokens=self._max_tokens,
            )
        except LLMUnavailableError:
            _log.info("self_model.tired")
            return None
        return parse_identity_delta(completion.content)

    def _render(self, current: IdentityDoc, inputs: ReflectionInputs) -> str:
        """Format the anchor + current self-model + reflection inputs for the role."""
        return (
            "Your anchor (fixed — stay consistent with it):\n"
            f"- name: {self._anchor.name}\n"
            f"- prime directive: {self._anchor.prime_directive}\n\n"
            "Your current self-model:\n"
            f"{current.self_model_doc}\n"
            f"- values: {current.values}\n"
            f"- concerns: {current.concerns}\n"
            f"- relationships: {current.relationships}\n\n"
            f"Recent experiences:\n{_bullets(inputs.recent_episodes)}\n\n"
            f"What you now know (consolidated facts):\n{_bullets(inputs.semantic_facts)}\n\n"
            f"Your recent mood: {inputs.mood or 'unremarkable'}\n"
            f"Your drives: {inputs.drives or 'at rest'}\n\n"
            "Reflect and produce the next version of your self-model as the required JSON."
        )


def _bullets(items: Sequence[str]) -> str:
    return "\n".join(f"- {item}" for item in items) if items else "- (nothing notable)"
