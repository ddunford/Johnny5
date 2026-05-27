"""The Metacognition agent — outcome review → reflection + self-improvement proposals.

Each sleep, ``Metacognition.review(window)`` inspects a window of how Johnny has
been functioning (goals resolved vs abandoned, degraded ticks, drive/mood
patterns) through the ``metacognition`` router role, and writes a first-person
review plus zero-or-more **proposals** to ``self_improvement_note``.

**It proposes only.** Applying a proposal — editing a prompt, retuning a drive,
changing code — is the Phase-9 gated self-edit flow, and is deliberately not wired
here: every note is persisted with ``status="open"`` and nothing consumes it to
act. ``parse_review`` is the pure projection (model JSON → typed review) the
contract test feeds a captured envelope through (FC-4 house rule). When the model
is tired the review degrades to an empty reflection with no proposals so the sleep
pipeline records the degradation and still wakes (per-stage isolation).
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from brain.config_store import ConfigStore, PromptNotFoundError, get_config_store
from brain.llm.base import LLMUnavailableError, Message
from brain.llm.router import LLMRouter
from brain.metacognition.store import MetacognitionStore, SelfImprovementNote
from foundation.config import get_settings
from foundation.observability import get_logger

_log = get_logger("brain.metacognition")

METACOGNITION_AGENT_NAME = "metacognition"
METACOGNITION_ROLE = "metacognition"

# Analytical, not creative — a low-ish temperature.
_TEMPERATURE = 0.5

_TIRED_REFLECTION = "I didn't have the clarity to review myself this cycle."


# ── inputs + the model response contract (pure projection) ─────────────────────


class ReviewWindow(BaseModel):
    """The window of Johnny's recent functioning the review considers.

    Built by the sleep orchestrator from the goal store + drive/mood state. Plain
    scalars/strings so the agent stays decoupled from those stores and is trivially
    testable.
    """

    goals_resolved: int = 0
    goals_abandoned: int = 0
    degraded_ticks: int = 0
    recent_goals: list[str] = Field(default_factory=list)
    mood: str = ""
    drives: str = ""


class Proposal(BaseModel):
    """One self-improvement proposal: what was noticed + the change to try."""

    observation: str
    proposal: str


class Review(BaseModel):
    """METACOGNITION's output: a first-person reflection + zero-or-more proposals."""

    reflection: str
    proposals: list[Proposal] = Field(default_factory=list)


def parse_review(content: str) -> Review:
    """Project a metacognition model response (JSON) into a ``Review`` (pure).

    The house-rule contract seam (TASK-4.13): a captured envelope fed through here
    asserts the projection, so an output-shape change surfaces here, not silently.
    """
    return Review.model_validate_json(content)


# ── the agent ────────────────────────────────────────────────────────────────


class Metacognition:
    """Reviews recent functioning and records self-improvement proposals (no apply)."""

    name = METACOGNITION_AGENT_NAME
    model_route = METACOGNITION_ROLE

    def __init__(
        self,
        router: LLMRouter | None = None,
        *,
        store: MetacognitionStore | None = None,
        config_store: ConfigStore | None = None,
        max_tokens: int | None = None,
    ) -> None:
        settings = get_settings()
        self._router = router
        self._store = store or MetacognitionStore()
        self.prompt = self._load_prompt(config_store)
        self._max_tokens = (
            max_tokens if max_tokens is not None else settings.metacognition_max_tokens
        )

    @staticmethod
    def _load_prompt(config_store: ConfigStore | None) -> str:
        try:
            return (config_store or get_config_store()).load_prompt(METACOGNITION_AGENT_NAME)
        except PromptNotFoundError:
            return ""

    async def review(self, window: ReviewWindow) -> Review:
        """Review the window and persist any proposals as ``open`` notes; return it.

        Each proposal becomes one ``self_improvement_note`` (status ``open``), with
        the overall reflection + window provenance in ``source`` so the reflection
        survives even when no proposal is made. Proposals are never applied here.
        """
        review = await self._reflect(window)
        provenance: dict[str, object] = {
            "reflection": review.reflection,
            "window": window.model_dump(),
        }
        for proposal in review.proposals:
            await self._store.add_note(
                SelfImprovementNote(
                    observation=proposal.observation,
                    proposal=proposal.proposal,
                    source=provenance,
                )
            )
        _log.info(
            "metacognition.review.done",
            proposals=len(review.proposals),
            resolved=window.goals_resolved,
            abandoned=window.goals_abandoned,
        )
        return review

    async def _reflect(self, window: ReviewWindow) -> Review:
        """Produce the review via the ``metacognition`` role; degrade when tired."""
        if self._router is None or not self.prompt:
            return Review(reflection=_TIRED_REFLECTION, proposals=[])
        messages = [
            Message(role="system", content=self.prompt),
            Message(role="user", content=self._render(window)),
        ]
        try:
            completion = await self._router.complete(
                METACOGNITION_ROLE,
                messages,
                schema=Review,
                temperature=_TEMPERATURE,
                max_tokens=self._max_tokens,
            )
        except LLMUnavailableError:
            _log.info("metacognition.tired")
            return Review(reflection=_TIRED_REFLECTION, proposals=[])
        return parse_review(completion.content)

    @staticmethod
    def _render(window: ReviewWindow) -> str:
        """Format the review window for the role."""
        goals = "\n".join(f"- {g}" for g in window.recent_goals) or "- (none of note)"
        return (
            "Here is a window of how you've been functioning lately:\n"
            f"- goals resolved: {window.goals_resolved}\n"
            f"- goals abandoned: {window.goals_abandoned}\n"
            f"- ticks where your cognition degraded: {window.degraded_ticks}\n"
            f"- recent goals:\n{goals}\n"
            f"- mood: {window.mood or 'unremarkable'}\n"
            f"- drives: {window.drives or 'at rest'}\n\n"
            "Review how your mind has been working and respond with the required JSON."
        )
