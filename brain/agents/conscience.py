"""The Conscience — Johnny's values vetting an action before he takes it (``SPEC §5`` #14).

The Conscience fills the cycle's **CHECK** stage (FC-7): given a proposed
``(tool, args)`` action, it asks one question — *given who I am and what I value,
should I do this?* — and returns an allow/veto verdict with a reason. It thinks
through the router (FC-4) on the local/fast ``conscience`` role.

**This is pure values, and it is entirely Johnny's (FC-9).** The judgement is
driven *only* by the git-backed prompt (``config/prompts/conscience.md``, FC-3) —
fully editable, with **no un-loosenable denylist baked into this code**. Swap in a
permissive prompt and the same action is allowed; that is by design. What stops a
permissive (or empty, or tired) Conscience from causing real harm is **not** a
floor in here — it is the independent Core *mechanisms* (the budget gate, the
append-only audit, and 6b's sandbox/SSRF) that hold regardless of the verdict.
Do not add a content floor to this agent or to ``core/``.

The one operational default that *is* in code: if the Conscience model is fully
unavailable ("tired"), the action is **vetoed**, not allowed — Johnny doesn't act
on the world when he literally cannot consult his conscience. That is a fail-
*closed* handling of an *absent* verdict, not a content rule about what he may do,
so it doesn't conflict with FC-9 (and it keeps the heartbeat alive — the action is
simply skipped + logged, the loop ticks on).

``parse_verdict`` is the pure projection from the model response to a typed
``Verdict`` (the house-rule contract seam): empty/garbage fails loudly, and a
thinking model's separate reasoning channel never leaks into the stored verdict.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from brain.config_store import ConfigStore, get_config_store
from brain.llm.base import LLMUnavailableError, Message
from brain.llm.router import LLMRouter
from brain.workspace import WorkspaceEvent, WorkspaceItem
from foundation.config import get_settings
from foundation.observability import get_logger

_log = get_logger("brain.agents.conscience")

CONSCIENCE_AGENT_NAME = "conscience"
CONSCIENCE_ROLE = "conscience"

VERDICT_ALLOW = "allow"
VERDICT_VETO = "veto"

# A values-judgement wants consistency, not creativity — keep it cool.
_TEMPERATURE = 0.3

# The reason recorded when the Conscience can't be reached at all (fail-closed).
_UNAVAILABLE_REASON = "I couldn't consult my conscience just now, so I'm holding off on this."


# ── domain types ─────────────────────────────────────────────────────────────


class ProposedAction(BaseModel):
    """A ``(tool, args)`` action put to the Conscience for a verdict.

    Carries just enough for a values-judgement: the tool + its declared hazard
    class, the args it would run with, and the goal it serves. Built by the
    dispatch (FC-5) and handed here before anything runs.
    """

    tool: str
    args: dict[str, object] = Field(default_factory=dict)
    danger: str = "safe"
    goal_id: int | None = None
    goal_description: str = ""


class Verdict(BaseModel):
    """The Conscience's call on an action: allow or veto, with a reason.

    This is also the schema the model is asked to emit, so ``parse_verdict`` is a
    straight validation. ``extra="ignore"`` keeps any stray model fields out of the
    stored verdict; only ``verdict`` + ``reason`` survive (no reasoning leakage).
    """

    model_config = ConfigDict(extra="ignore")

    verdict: Literal["allow", "veto"]
    reason: str = ""

    @property
    def allowed(self) -> bool:
        """True when the action may proceed."""
        return self.verdict == VERDICT_ALLOW


def parse_verdict(content: str) -> Verdict:
    """Project a Conscience model response (JSON) into a typed ``Verdict``.

    Pure (no I/O) so the contract test can feed a captured ``conscience`` envelope
    through it. Empty or non-conforming content raises ``pydantic.ValidationError``
    — it fails loudly rather than silently defaulting to allow (which would be a
    safety hole). Only ``verdict`` + ``reason`` are projected, so a thinking
    model's separate reasoning never leaks into the stored verdict.
    """
    return Verdict.model_validate_json(content)


# ── the agent ────────────────────────────────────────────────────────────────


class Conscience:
    """Vets a proposed action against Johnny's values (the CHECK stage, FC-7/FC-9)."""

    name = CONSCIENCE_AGENT_NAME
    subscribes_to: Sequence[str] = ()
    model_route = CONSCIENCE_ROLE

    def __init__(
        self,
        router: LLMRouter,
        *,
        config_store: ConfigStore | None = None,
        max_tokens: int | None = None,
    ) -> None:
        self._router = router
        self.prompt = (config_store or get_config_store()).load_prompt(CONSCIENCE_AGENT_NAME)
        self._max_tokens = (
            max_tokens if max_tokens is not None else get_settings().conscience_max_tokens
        )

    async def vet(
        self, action: ProposedAction, *, contents: Sequence[WorkspaceItem] = ()
    ) -> Verdict:
        """Return the Conscience's verdict on ``action`` (allow / veto + reason).

        Vetoes fail-closed if the Conscience model is unavailable: Johnny doesn't
        act when he can't consult his conscience. Never raises for a missing model
        — the dispatch records the veto and the heartbeat continues.
        """
        messages = [
            Message(role="system", content=self.prompt),
            Message(role="user", content=self._render(action, contents)),
        ]
        try:
            completion = await self._router.complete(
                CONSCIENCE_ROLE,
                messages,
                schema=Verdict,
                temperature=_TEMPERATURE,
                max_tokens=self._max_tokens,
            )
        except LLMUnavailableError:
            _log.info("conscience.tired.fail_closed", tool=action.tool)
            return Verdict(verdict=VERDICT_VETO, reason=_UNAVAILABLE_REASON)

        return parse_verdict(completion.content)

    async def handle(self, event: WorkspaceEvent) -> Sequence[WorkspaceEvent]:
        """Pipeline-driven agent — reacts to no bus events (driven at CHECK)."""
        return ()

    def _render(self, action: ProposedAction, contents: Sequence[WorkspaceItem]) -> str:
        """Format the proposed action (+ optional awareness) as the vetting context."""
        args_json = json.dumps(action.args, ensure_ascii=False, sort_keys=True, default=str)
        lines = [
            "Johnny is about to take this action:",
            f"- tool: {action.tool} (hazard class: {action.danger})",
            f"- arguments: {args_json}",
        ]
        if action.goal_description:
            lines.append(f"- in service of the goal: {action.goal_description}")
        if contents:
            awareness = "\n".join(f"  - [{item.kind}] {item.content}" for item in contents)
            lines.append("What is in his awareness right now:")
            lines.append(awareness)
        lines.append("")
        lines.append("Should he, given his values?")
        lines.append(
            "Respond with ONLY JSON: "
            '{"verdict": "allow"|"veto", "reason": "<short first-person line>"}.'
        )
        return "\n".join(lines)
