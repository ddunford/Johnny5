"""``code_exec`` — run a Python snippet in the hardened sandbox (``danger:exec``).

Johnny's Mastery drive can want to *do* something computational — work a problem,
check an idea. This tool lets him run Python, but **never on the host**: it
dispatches the snippet into the throw-away ``johnny5-sandbox`` container via the
devops-owned launcher ``ops/sandbox/run-sandbox.sh`` (TASK-6b.6a), which is the
single security boundary (``--network none``, read-only rootfs, dropped caps,
non-root, memory/cpu/pids caps, timeout-kill). This module never issues ``docker``
itself and never trusts the snippet — the container contains it.

The launcher contract (``ops/sandbox/README.md``) is fixed and transport-agnostic:
stdin = UTF-8 source, stdout = exactly one JSON verdict line, and it *always* emits
a well-formed verdict — for success, a user exception, a timeout, a resource kill,
**or** its own infra failure (``error.type = "SandboxUnavailable"``, exit 3). So the
tool's job is small: feed the snippet in, read the one verdict out, and project it
onto a typed ``ToolResult``. Every failure mode (sandbox down, snippet error,
timeout, OOM) comes back as a graceful ``success=False`` result — never a crash that
takes down the cognitive cycle.

``parse_sandbox_verdict`` is the pure projection from the launcher's JSON to a typed
``SandboxVerdict`` — the house-rule contract seam, pinned by a fixture captured from
the launcher's documented envelope, so a launcher output-shape change surfaces in a
test rather than silently in production.
"""

from __future__ import annotations

import asyncio
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from brain.effectors.tools import DangerClass, ToolResult
from foundation.config import get_settings
from foundation.observability import get_logger

_log = get_logger("brain.effectors.code_exec")

CODE_EXEC_TOOL_NAME = "code_exec"


class SandboxRunError(Exception):
    """The launcher could not be run to a verdict at all (not a sandbox *verdict*).

    Distinct from a sandbox verdict that reports failure (user error / timeout /
    OOM — those come back as a well-formed ``SandboxVerdict``). This is the launcher
    process itself failing to produce any parseable line — e.g. it isn't found, or
    it wedged past the outer wait and had to be killed. The tool turns it into a
    graceful failed ``ToolResult``; it never escapes to the cycle.
    """


# ── the launcher verdict contract (the ServerEnvelope-style seam) ──────────────


class SandboxVerdict(BaseModel):
    """The JSON envelope ``run-sandbox.sh`` emits (``ops/sandbox/README.md``).

    ``extra="ignore"`` so a launcher that adds a field can't break the projection;
    the fields the tool depends on are modelled explicitly.
    """

    model_config = ConfigDict(extra="ignore")

    ok: bool
    timed_out: bool = False
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    truncated: bool = False
    error: dict[str, object] | None = None
    duration_ms: int = 0


def parse_sandbox_verdict(line: str) -> SandboxVerdict:
    """Project one launcher JSON line into a typed ``SandboxVerdict`` (pure, no I/O).

    Pinned by a contract test fed a captured launcher envelope, so a shape change in
    ``run-sandbox.sh`` surfaces there rather than silently in production (FC-4 house rule).
    """
    return SandboxVerdict.model_validate_json(line)


# ── the runner (how the snippet reaches the launcher; injectable for tests) ────


@runtime_checkable
class SandboxRunner(Protocol):
    """Runs ``code`` in the sandbox and returns the launcher's raw verdict JSON line."""

    async def run(self, code: str, *, timeout_seconds: int) -> str: ...


class SubprocessSandboxRunner:
    """Default runner: pipe the snippet to ``run-sandbox.sh`` and read its verdict.

    The launcher owns the container + every hardening flag; this only feeds stdin,
    reads the one stdout verdict line, and enforces an *outer* wall-clock wait a few
    seconds beyond the launcher's own hard backstop — so the launcher virtually
    always wins and returns structured JSON, and the outer kill is a last resort that
    still degrades gracefully (raising ``SandboxRunError``, never hanging the cycle).
    """

    def __init__(
        self,
        *,
        launcher_path: str | None = None,
        outer_buffer_seconds: float | None = None,
    ) -> None:
        settings = get_settings()
        self._launcher = launcher_path or settings.sandbox_launcher_path
        self._outer_buffer = (
            outer_buffer_seconds
            if outer_buffer_seconds is not None
            else settings.code_exec_outer_buffer_seconds
        )

    async def run(self, code: str, *, timeout_seconds: int) -> str:
        try:
            proc = await asyncio.create_subprocess_exec(
                self._launcher,
                str(timeout_seconds),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except (OSError, ValueError) as exc:  # launcher missing / not executable
            raise SandboxRunError(f"could not start the sandbox launcher: {exc}") from exc

        outer_wait = timeout_seconds + self._outer_buffer
        try:
            stdout, _ = await asyncio.wait_for(
                proc.communicate(code.encode("utf-8")), timeout=outer_wait
            )
        except TimeoutError as exc:
            # The launcher should have returned within its own backstop; it didn't.
            # Kill it (its trap reaps the container) and fail gracefully.
            proc.kill()
            await asyncio.gather(proc.wait(), return_exceptions=True)
            raise SandboxRunError(
                f"sandbox launcher did not return within {outer_wait:.0f}s"
            ) from exc

        verdict = _last_json_line(stdout.decode("utf-8", errors="replace"))
        if verdict is None:
            raise SandboxRunError("sandbox launcher produced no verdict line")
        return verdict


def _last_json_line(text: str) -> str | None:
    """The last non-empty line of ``text`` (the verdict; diagnostics go to stderr)."""
    for line in reversed(text.splitlines()):
        if line.strip():
            return line.strip()
    return None


# ── the tool ──────────────────────────────────────────────────────────────────


class CodeExecArgs(BaseModel):
    """Args for ``code_exec``: the ``code`` to run + an optional in-container timeout."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1)
    timeout_seconds: int | None = Field(default=None, ge=1, le=60)

    @property
    def code_chars(self) -> int:
        return len(self.code)


class CodeExecTool:
    """Run a Python snippet in the isolated sandbox container (``danger:exec``)."""

    name = CODE_EXEC_TOOL_NAME
    danger = DangerClass.EXEC
    args_schema = CodeExecArgs

    def __init__(
        self,
        *,
        runner: SandboxRunner | None = None,
        default_timeout_seconds: int | None = None,
        max_code_chars: int | None = None,
    ) -> None:
        settings = get_settings()
        self._runner = runner or SubprocessSandboxRunner()
        self._default_timeout = (
            default_timeout_seconds
            if default_timeout_seconds is not None
            else settings.code_exec_timeout_seconds
        )
        self._max_code_chars = (
            max_code_chars if max_code_chars is not None else settings.code_exec_max_code_chars
        )

    async def run(self, args: CodeExecArgs) -> ToolResult:
        if args.code_chars > self._max_code_chars:
            return ToolResult(
                success=False,
                output={"error": "too_large", "chars": args.code_chars},
                summary=f"snippet too large ({args.code_chars} > {self._max_code_chars} chars)",
            )

        timeout = args.timeout_seconds or self._default_timeout
        try:
            line = await self._runner.run(args.code, timeout_seconds=timeout)
            verdict = parse_sandbox_verdict(line)
        except SandboxRunError as exc:
            _log.info("code_exec.unavailable", reason=str(exc))
            return ToolResult(
                success=False,
                output={"error": "sandbox_unavailable", "reason": str(exc)},
                summary=f"could not run code in the sandbox — {exc}",
            )

        return ToolResult(
            success=verdict.ok,
            output={
                "stdout": verdict.stdout,
                "stderr": verdict.stderr,
                "exit_code": verdict.exit_code,
                "timed_out": verdict.timed_out,
                "truncated": verdict.truncated,
                "error": verdict.error,
                "duration_ms": verdict.duration_ms,
            },
            summary=_summarise(verdict),
        )


def _summarise(verdict: SandboxVerdict) -> str:
    """A one-line trace of how the run went (for the bus + audit)."""
    if verdict.ok:
        return f"ran code in the sandbox ({verdict.duration_ms}ms)"
    if verdict.timed_out:
        return "code timed out in the sandbox"
    error = verdict.error or {}
    kind = error.get("type", "error") if isinstance(error, dict) else "error"
    return f"code failed in the sandbox ({kind})"
