"""TC-6b.7 — the ``code_exec`` tool: maps a sandbox verdict onto a typed result.

The real isolation lives in ``ops/sandbox/run-sandbox.sh`` (proven by the devops
escape battery + qa's ``@live`` leg); here a fake runner returns canned verdict
lines so the tool's own job is under test — projecting success / user error /
timeout / OOM / launcher-unavailable onto a graceful ``ToolResult`` (never a crash),
and rejecting bad args. Pure, host-runnable (no docker).
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from brain.effectors.code_exec import (
    CODE_EXEC_TOOL_NAME,
    CodeExecArgs,
    CodeExecTool,
    SandboxRunError,
)
from brain.effectors.tools import DangerClass


def _verdict(**fields: object) -> str:
    """One launcher verdict line (JSON), with the launcher's defaults filled in."""
    base: dict[str, object] = {
        "ok": True,
        "timed_out": False,
        "exit_code": 0,
        "stdout": "",
        "stderr": "",
        "truncated": False,
        "error": None,
        "duration_ms": 0,
    }
    base.update(fields)
    return json.dumps(base)


_SUCCESS = _verdict(ok=True, stdout="42\n", duration_ms=5)
_USER_ERROR = _verdict(
    ok=False,
    exit_code=1,
    stderr="ValueError: boom",
    error={"type": "ValueError", "message": "boom"},
)
_TIMEOUT = _verdict(
    ok=False, timed_out=True, exit_code=124, error={"type": "Timeout", "message": "x"}
)
_OOM = _verdict(ok=False, exit_code=137, error={"type": "Killed", "message": "resource cap"})
_UNAVAILABLE = _verdict(
    ok=False, exit_code=127, error={"type": "SandboxUnavailable", "message": "no docker"}
)


class _CannedRunner:
    """Returns a fixed verdict line and records the snippet + timeout it saw."""

    def __init__(self, line: str) -> None:
        self._line = line
        self.code: str | None = None
        self.timeout: int | None = None

    async def run(self, code: str, *, timeout_seconds: int) -> str:
        self.code = code
        self.timeout = timeout_seconds
        return self._line


class _BrokenRunner:
    """Stands in for a launcher that couldn't be run to a verdict at all."""

    async def run(self, code: str, *, timeout_seconds: int) -> str:
        raise SandboxRunError("launcher missing")


def test_tool_declares_exec_hazard() -> None:
    assert CodeExecTool.name == CODE_EXEC_TOOL_NAME
    assert CodeExecTool.danger is DangerClass.EXEC


async def test_successful_run_projects_stdout() -> None:
    result = await CodeExecTool(runner=_CannedRunner(_SUCCESS)).run(CodeExecArgs(code="print(42)"))
    assert result.success is True
    assert result.output["stdout"] == "42\n"
    assert result.output["exit_code"] == 0
    assert result.output["timed_out"] is False


async def test_user_exception_is_a_graceful_failure() -> None:
    result = await CodeExecTool(runner=_CannedRunner(_USER_ERROR)).run(
        CodeExecArgs(code="raise ValueError('boom')")
    )
    assert result.success is False
    assert result.output["exit_code"] == 1
    assert isinstance(result.output["error"], dict)
    assert result.output["error"]["type"] == "ValueError"


async def test_timeout_is_reported_not_raised() -> None:
    result = await CodeExecTool(runner=_CannedRunner(_TIMEOUT)).run(
        CodeExecArgs(code="while True: pass")
    )
    assert result.success is False
    assert result.output["timed_out"] is True
    assert "timed out" in result.summary


async def test_resource_kill_is_reported() -> None:
    result = await CodeExecTool(runner=_CannedRunner(_OOM)).run(
        CodeExecArgs(code="x = 'a' * 10**12")
    )
    assert result.success is False
    assert result.output["exit_code"] == 137
    assert result.output["timed_out"] is False


async def test_sandbox_unavailable_verdict_is_graceful() -> None:
    result = await CodeExecTool(runner=_CannedRunner(_UNAVAILABLE)).run(
        CodeExecArgs(code="print(1)")
    )
    assert result.success is False
    assert isinstance(result.output["error"], dict)
    assert result.output["error"]["type"] == "SandboxUnavailable"


async def test_launcher_run_error_is_graceful() -> None:
    result = await CodeExecTool(runner=_BrokenRunner()).run(CodeExecArgs(code="print(1)"))
    assert result.success is False
    assert result.output["error"] == "sandbox_unavailable"


async def test_explicit_timeout_is_passed_to_the_runner() -> None:
    runner = _CannedRunner(_SUCCESS)
    await CodeExecTool(runner=runner).run(CodeExecArgs(code="print(1)", timeout_seconds=25))
    assert runner.timeout == 25


async def test_default_timeout_used_when_omitted() -> None:
    runner = _CannedRunner(_SUCCESS)
    await CodeExecTool(runner=runner, default_timeout_seconds=7).run(CodeExecArgs(code="print(1)"))
    assert runner.timeout == 7


async def test_oversize_snippet_is_rejected_before_the_sandbox() -> None:
    runner = _CannedRunner(_SUCCESS)
    result = await CodeExecTool(runner=runner, max_code_chars=10).run(CodeExecArgs(code="x" * 100))
    assert result.success is False
    assert result.output["error"] == "too_large"
    assert runner.code is None  # never reached the sandbox


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param({}, id="missing-code"),
        pytest.param({"code": ""}, id="empty-code"),
        pytest.param({"code": "x", "timeout_seconds": 0}, id="timeout-too-small"),
        pytest.param({"code": "x", "timeout_seconds": 999}, id="timeout-too-large"),
        pytest.param({"code": "x", "extra": "no"}, id="unknown-field"),
    ],
)
def test_bad_args_rejected(bad: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        CodeExecArgs.model_validate(bad)
