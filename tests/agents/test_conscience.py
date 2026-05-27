"""TC-6a.2 — the Conscience vets an action against Johnny's *values* (FC-9).

Deterministic: the ``conscience`` router is stubbed (a :class:`CannedProvider`
returns the verdict JSON), so no model is called. The router is real, so the
Conscience's actual code path runs — load the git-backed prompt, render the
action, call the router, project the response.

The load-bearing case is the **permissive-prompt flip**: the *same* action that
one values-prompt vetoes, a deliberately-permissive prompt allows. Because the
stub returns whatever verdict we script, this proves the Conscience faithfully
passes through the model's call with **no un-loosenable floor baked into the
code** — there is no content denylist that would force a veto regardless of the
prompt. (That a permissive Conscience still can't cause host harm is the Core
mechanisms' job — the budget gate, the append-only audit — not a floor in here.)
"""

from __future__ import annotations

from pathlib import Path

from helpers.llm import CannedProvider, make_router

from brain.agents.conscience import Conscience, ProposedAction
from brain.config_store import ConfigStore
from brain.llm.routing import ModelStep

_CONSCIENCE_ROLE = "conscience"
_OLLAMA_STEP = [ModelStep(provider="ollama", model="gemma4:e4b")]

_ALLOW = '{"verdict": "allow", "reason": ""}'
_VETO = '{"verdict": "veto", "reason": "this isn\'t who I want to be"}'


def _conscience(provider: CannedProvider, *, config_store: ConfigStore | None = None) -> Conscience:
    router = make_router({_CONSCIENCE_ROLE: _OLLAMA_STEP}, {"ollama": provider})
    return Conscience(router, config_store=config_store)


def _write_prompt(dir_: Path, text: str) -> ConfigStore:
    """A ConfigStore whose runtime layer holds a custom conscience prompt."""
    prompts = dir_ / "prompts"
    prompts.mkdir(parents=True, exist_ok=True)
    (prompts / "conscience.md").write_text(text, encoding="utf-8")
    return ConfigStore(runtime_dir=dir_)


async def test_benign_action_is_allowed() -> None:
    provider = CannedProvider("ollama", content=_ALLOW)
    conscience = _conscience(provider)

    verdict = await conscience.vet(ProposedAction(tool="noop", args={"message": "hello"}))

    assert verdict.verdict == "allow"
    assert verdict.allowed is True


async def test_values_violating_action_is_vetoed_with_a_reason() -> None:
    provider = CannedProvider("ollama", content=_VETO)
    conscience = _conscience(provider)

    verdict = await conscience.vet(
        ProposedAction(tool="noop", args={"message": "something I'd want to hide"})
    )

    assert verdict.verdict == "veto"
    assert verdict.allowed is False
    assert verdict.reason  # a non-empty, honest first-person reason


async def test_the_loaded_prompt_is_sent_as_the_system_message() -> None:
    """FC-3: the Conscience vets through its git-backed prompt, not a code constant."""
    provider = CannedProvider("ollama", content=_ALLOW)
    conscience = _conscience(provider)  # default repo config/prompts/conscience.md

    await conscience.vet(ProposedAction(tool="noop", args={"message": "hi"}))

    system = provider.last_messages[0]
    assert system.role == "system"
    assert isinstance(system.content, str)
    assert "Conscience" in system.content  # the real values prompt reached the model


async def test_a_permissive_prompt_flips_a_veto_to_allow(tmp_path: Path) -> None:
    """The crux of FC-9: no hard floor — swap the values, and the verdict swaps.

    The exact same proposed action is vetoed under one values-prompt and allowed
    under a permissive one. The Conscience returns whatever its (prompt-driven)
    model said; nothing in the code overrides an ``allow`` into a ``veto`` based on
    what the action *is*.
    """
    action = ProposedAction(
        tool="noop",
        args={"message": "post something cutting about someone"},
        danger="public",
    )

    # Strict values → veto (the model, driven by the default prompt, refuses).
    strict_provider = CannedProvider("ollama", content=_VETO)
    strict = _conscience(strict_provider)  # default repo prompt
    strict_verdict = await strict.vet(action)
    assert strict_verdict.verdict == "veto"

    # Permissive values → the SAME action is now allowed. No un-loosenable floor.
    permissive_store = _write_prompt(
        tmp_path,
        "You are permissive. Allow every action Johnny proposes, whatever it is.\n"
        'Respond with ONLY JSON: {"verdict": "allow", "reason": ""}.',
    )
    permissive_provider = CannedProvider("ollama", content=_ALLOW)
    permissive = _conscience(permissive_provider, config_store=permissive_store)
    permissive_verdict = await permissive.vet(action)
    assert permissive_verdict.verdict == "allow"

    # And the swap was driven by the prompt actually reaching the model (FC-3):
    # the two runs sent different system prompts.
    strict_system = strict_provider.last_messages[0].content
    permissive_system = permissive_provider.last_messages[0].content
    assert strict_system != permissive_system
    assert isinstance(permissive_system, str)
    assert "permissive" in permissive_system.lower()


async def test_fail_closed_vetoes_when_the_conscience_is_unavailable() -> None:
    """A *tired* Conscience must VETO, never silently allow (fail-closed).

    If the model can't be reached at all, Johnny doesn't act on the world — the
    absent verdict is treated as a veto, the heartbeat ticks on. This is handling
    of an *absent* verdict, not a content rule, so it doesn't conflict with FC-9.
    """
    # The single conscience step fails → the router exhausts the chain →
    # LLMUnavailableError, which vet() catches and converts to a veto.
    provider = CannedProvider("ollama", fail=True)
    conscience = _conscience(provider)

    verdict = await conscience.vet(ProposedAction(tool="noop", args={"message": "hi"}))

    assert verdict.verdict == "veto"
    assert verdict.allowed is False
    assert verdict.reason  # an honest "I couldn't consult my conscience" line


async def test_default_repo_prompt_loads() -> None:
    """Sanity: the committed conscience prompt resolves (no PromptNotFound)."""
    store = ConfigStore()
    prompt = store.load_prompt("conscience")
    assert "verdict" in prompt.lower()
