"""TC-6b.7 (contract) — pin ``code_exec``'s ``SandboxVerdict`` to the launcher envelope.

The house-rule contract seam: ``parse_sandbox_verdict`` is fed captured
``run-sandbox.sh`` verdict envelopes (``tests/fixtures/sandbox/verdicts.json`` — the
launcher README's documented observed shapes) and must project each without
throwing. A shape change in the launcher breaks THIS test, not production. qa's
``@live`` leg (TASK-6b.12) recaptures fresh envelopes from the real container.
Pure, host-runnable.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest

from brain.effectors.code_exec import parse_sandbox_verdict

_CASES = ("success", "user_exception", "timeout", "oom_resource_kill", "sandbox_unavailable")


@pytest.mark.parametrize("case", _CASES)
def test_parse_projects_every_captured_envelope(
    case: str, load_fixture: Callable[[str], Any]
) -> None:
    envelope = load_fixture("sandbox/verdicts.json")[case]
    verdict = parse_sandbox_verdict(json.dumps(envelope))

    # Every field the tool depends on projects from the real wire shape.
    assert verdict.ok == envelope["ok"]
    assert verdict.exit_code == envelope["exit_code"]
    assert verdict.timed_out == envelope["timed_out"]
    assert verdict.stdout == envelope["stdout"]
    assert verdict.error == envelope["error"]


def test_success_envelope_is_ok(load_fixture: Callable[[str], Any]) -> None:
    verdict = parse_sandbox_verdict(json.dumps(load_fixture("sandbox/verdicts.json")["success"]))
    assert verdict.ok is True
    assert verdict.error is None


def test_timeout_envelope_flags_timed_out(load_fixture: Callable[[str], Any]) -> None:
    verdict = parse_sandbox_verdict(json.dumps(load_fixture("sandbox/verdicts.json")["timeout"]))
    assert verdict.ok is False
    assert verdict.timed_out is True
    assert verdict.exit_code == 124


def test_unknown_extra_fields_are_ignored() -> None:
    # extra="ignore" — a launcher that adds a field can't break the projection.
    verdict = parse_sandbox_verdict('{"ok": true, "future_field": 42}')
    assert verdict.ok is True
