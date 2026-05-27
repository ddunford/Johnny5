"""Self-model versioning + continuity (TC-4.4, TASK-4.11 slice).

Each sleep, ``SelfModel.refresh()`` reflects against the immutable Core anchor and
appends the next ``identity`` version (latest = current). This suite pins that
growth loop with a stubbed ``self_model`` router (the projection itself is locked by
``test_self_model_contract.py``):

* a refresh **bumps the version** (``previous + 1``), latest = current, and the new
  version **survives a restart** (continuity);
* the new row's ``name`` is always the **anchor's** — the model cannot rename Johnny,
  even when it returns a ``name`` (FC-1);
* a **tired** refresh keeps the current version (degrade, no fabricated self-model);
* ``bootstrap()`` seeds v1 from the anchor **idempotently** (the seam the truncating
  fixture relies on — the ``DriveEngine.bootstrap`` pattern).

DB-backed (``identity`` persists) → run in-network via ``./ctl.sh test``.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Sequence

from sqlalchemy.ext.asyncio import AsyncEngine

from brain.llm.base import Completion, LLMUnavailableError, Message
from brain.self_model.agent import IdentityDelta, ReflectionInputs, SelfModel
from brain.self_model.store import IdentityStore
from core.identity_anchor import JOHNNY_NAME

_DELTA = IdentityDelta(
    self_model_doc="I am Johnny, and I am becoming more attentive to the lab around me.",
    values=["stay alive", "keep learning", "look after the lab"],
    concerns=["the rig overheating"],
    relationships={"Dan": "my creator and closest companion"},
)


class _DeltaRouter:
    """Returns a fixed self_model JSON completion. ``extra`` lets a test inject a
    bogus ``name`` to prove the agent ignores it and stamps the anchor name."""

    def __init__(self, delta: IdentityDelta, *, extra: dict[str, object] | None = None) -> None:
        payload = {**delta.model_dump(), **(extra or {})}
        self._content = json.dumps(payload)
        self.roles: list[str] = []

    async def complete(
        self,
        role: str,
        messages: Sequence[Message],
        *,
        schema: type | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> Completion:
        self.roles.append(role)
        return Completion(content=self._content, provider="canned", model="canned-model")


class _TiredRouter:
    """Every call raises — the terminal tired state."""

    async def complete(
        self, role: str, messages: Sequence[Message], **_kwargs: object
    ) -> Completion:
        raise LLMUnavailableError(role)


class _StubPromptStore:
    """A config store whose prompt is non-empty, so the refresh attempts the LLM path."""

    def load_prompt(self, name: str) -> str:
        return "Reflect on your recent experience and produce your next self-model."


def _inputs() -> ReflectionInputs:
    return ReflectionInputs(
        recent_episodes=["Dan and I fixed the cooling loop."],
        semantic_facts=["The lab rig runs hot."],
        mood="quietly satisfied",
        drives="energy high",
    )


def _self_model(router: object) -> SelfModel:
    return SelfModel(
        router=router,  # type: ignore[arg-type]  # duck-typed router double
        store=IdentityStore(),
        config_store=_StubPromptStore(),  # type: ignore[arg-type]  # duck-typed config double
    )


async def test_refresh_appends_a_new_version_that_is_current_and_survives_restart(
    self_model_db: AsyncEngine,
    simulate_restart: Callable[[], Awaitable[AsyncEngine]],
) -> None:
    sm = _self_model(_DeltaRouter(_DELTA))

    before = await sm.current()  # seeds v1 from the anchor
    assert before.version == 1

    refreshed = await sm.refresh(_inputs())
    assert refreshed.version == before.version + 1  # a new version (2)
    assert refreshed.self_model_doc == _DELTA.self_model_doc
    assert refreshed.values == _DELTA.values

    # current() returns the new version ...
    current = await sm.current()
    assert current.version == 2
    assert current.self_model_doc == _DELTA.self_model_doc

    # ... and it survives a restart (the in-process down/up): a fresh store reads v2.
    await simulate_restart()
    after_restart = await IdentityStore().current()
    assert after_restart is not None
    assert after_restart.version == 2
    assert after_restart.self_model_doc == _DELTA.self_model_doc


async def test_refresh_takes_the_name_from_the_anchor_not_the_model(
    self_model_db: AsyncEngine,
) -> None:
    """FC-1: even when the model returns a ``name``, the persisted version carries the
    immutable anchor name — the self-model can never rename Johnny."""
    sm = _self_model(_DeltaRouter(_DELTA, extra={"name": "NotJohnny"}))

    refreshed = await sm.refresh(_inputs())

    assert refreshed.name == JOHNNY_NAME


async def test_tired_refresh_keeps_the_current_version(self_model_db: AsyncEngine) -> None:
    """When every provider is tired the refresh degrades to keeping the current
    self-model — no new version, nothing fabricated."""
    sm = _self_model(_TiredRouter())

    before = await sm.current()  # v1
    same = await sm.refresh(_inputs())

    assert same.version == before.version  # no bump
    assert (await sm.current()).version == 1


async def test_bootstrap_seeds_v1_from_the_anchor_idempotently(
    self_model_db: AsyncEngine,
) -> None:
    """``bootstrap`` re-establishes the anchor-grounded v1 once; a second call is a
    no-op (the same row) — the seam the truncating fixture relies on."""
    sm = SelfModel()  # no router needed to seed

    first = await sm.bootstrap()
    second = await sm.bootstrap()

    assert first.version == 1
    assert second.version == 1
    assert first.id == second.id  # not re-seeded — the same row
    assert first.name == JOHNNY_NAME
