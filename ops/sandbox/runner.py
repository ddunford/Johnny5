"""code_exec sandbox runner — the in-container entrypoint.

Reads Python source from stdin, executes it, and writes exactly ONE JSON result
envelope to the container's real stdout. Runs as the image ENTRYPOINT inside the
hardened ``johnny5-sandbox`` container (no network, read-only rootfs, dropped
caps, non-root, resource-capped — see ``ops/sandbox/run-sandbox.sh``).

Security posture (deliberate): this runner does NOT try to sandbox Python at the
language level (restricted ``__builtins__``, import blocking, etc.). That is
well known to be bypassable and gives false confidence. The *container* is the
only trust boundary — the user code gets full, ordinary Python, and the
container's isolation + caps are what contain it. The runner's job is purely to
(1) capture output robustly, (2) bound output size, (3) enforce an in-container
timeout as defence-in-depth (the launcher's wall-clock kill is authoritative),
and (4) always emit a well-formed verdict so the caller can parse one shape.

Stdlib only — the image carries nothing else.
"""

from __future__ import annotations

import base64
import builtins
import contextlib
import json
import os
import resource
import signal
import sys
import tempfile
import time
import traceback
from typing import BinaryIO

OUTPUT_MAX = int(os.environ.get("SANDBOX_OUTPUT_MAX", "65536"))
TIMEOUT = int(os.environ.get("SANDBOX_RUN_TIMEOUT", "10"))

# Duplicate the real stdout fd up front so we can always emit the verdict there,
# no matter how the user code mangles sys.stdout / fd 1.
_VERDICT_FD = os.dup(1)


def _emit(obj: dict) -> None:
    os.write(_VERDICT_FD, (json.dumps(obj, default=str) + "\n").encode("utf-8"))


class _Timeout(BaseException):
    """Raised on SIGALRM/SIGXCPU. Derives from BaseException so a user
    ``except Exception`` can't swallow the timeout."""


def _on_timeout(_signum: int, _frame: object) -> None:  # signal handler signature
    raise _Timeout()


def _set_resource_limits() -> None:
    # Defence-in-depth alongside docker --cpus/--memory/--pids. Best-effort:
    # an unprivileged process can only lower limits, and some platforms refuse
    # a few of these — never let a failed setrlimit abort the run.
    for res, soft, hard in (
        (resource.RLIMIT_CPU, TIMEOUT, TIMEOUT + 1),  # CPU-seconds hard stop
        (resource.RLIMIT_NPROC, 64, 64),  # thread/proc fan-out
        (resource.RLIMIT_FSIZE, 8 * 1024 * 1024, 8 * 1024 * 1024),  # max file write
        (resource.RLIMIT_CORE, 0, 0),  # no core dumps
    ):
        with contextlib.suppress(ValueError, OSError):
            resource.setrlimit(res, (soft, hard))


def _read_source() -> str:
    """The snippet, from ``SANDBOX_CODE_B64`` (base64) if set, else stdin.

    The launcher passes code via env so it can run the container DETACHED and
    never needs the Docker attach/exec endpoints (kept off the socket-proxy
    allowlist). Stdin remains a fallback for direct ``docker run -i`` debugging.
    """
    b64 = os.environ.get("SANDBOX_CODE_B64")
    if b64:
        return base64.b64decode(b64).decode("utf-8", "replace")
    return sys.stdin.buffer.read().decode("utf-8", "replace")


def _read_capped(fobj: BinaryIO) -> tuple[str, bool]:
    fobj.flush()
    fobj.seek(0)
    raw = fobj.read(OUTPUT_MAX + 1)
    truncated = len(raw) > OUTPUT_MAX
    text = raw[:OUTPUT_MAX].decode("utf-8", "replace")
    if truncated:
        text += "\n…[output truncated]"
    return text, truncated


def main() -> None:
    source = _read_source()

    result: dict = {
        "ok": True,
        "timed_out": False,
        "exit_code": 0,
        "stdout": "",
        "stderr": "",
        "truncated": False,
        "error": None,
        "duration_ms": 0,
    }

    signal.signal(signal.SIGALRM, _on_timeout)
    with contextlib.suppress(ValueError, OSError, AttributeError):
        signal.signal(signal.SIGXCPU, _on_timeout)
    signal.alarm(TIMEOUT)

    # Capture stdout/stderr at the fd level (catches os.write(1, ...) and
    # C-level writes, not just sys.stdout). tempfiles live on the /tmp tmpfs.
    with tempfile.TemporaryFile() as out_f, tempfile.TemporaryFile() as err_f:
        saved_out, saved_err = os.dup(1), os.dup(2)
        start = time.monotonic()
        try:
            os.dup2(out_f.fileno(), 1)
            os.dup2(err_f.fileno(), 2)
            _set_resource_limits()
            namespace = {"__name__": "__main__", "__builtins__": builtins}
            exec(compile(source, "<sandbox>", "exec"), namespace)  # noqa: S102 — by design
        except _Timeout:
            result.update(
                ok=False,
                timed_out=True,
                exit_code=124,
                error={"type": "Timeout", "message": f"exceeded {TIMEOUT}s of CPU/wall time"},
            )
        except SystemExit as exc:
            code = exc.code
            rc = code if isinstance(code, int) else (0 if code is None else 1)
            result["exit_code"] = rc
            result["ok"] = rc == 0
            if rc != 0 and not isinstance(code, int):
                result["error"] = {"type": "SystemExit", "message": str(code)[:2000]}
        except BaseException as exc:  # noqa: BLE001 — surface everything as a verdict
            with contextlib.suppress(Exception):
                sys.stderr.write(traceback.format_exc())
            result.update(
                ok=False,
                exit_code=1,
                error={"type": type(exc).__name__, "message": str(exc)[:2000]},
            )
        finally:
            signal.alarm(0)
            with contextlib.suppress(Exception):
                sys.stdout.flush()
                sys.stderr.flush()
            os.dup2(saved_out, 1)
            os.dup2(saved_err, 2)
            os.close(saved_out)
            os.close(saved_err)

        result["duration_ms"] = int((time.monotonic() - start) * 1000)
        out_text, out_trunc = _read_capped(out_f)
        err_text, err_trunc = _read_capped(err_f)

    result["stdout"] = out_text
    result["stderr"] = err_text
    result["truncated"] = out_trunc or err_trunc
    _emit(result)


if __name__ == "__main__":
    try:
        main()
    except BaseException as exc:  # noqa: BLE001 — last resort: never exit without a verdict
        _emit(
            {
                "ok": False,
                "timed_out": False,
                "exit_code": 1,
                "stdout": "",
                "stderr": "",
                "truncated": False,
                "error": {"type": "RunnerError", "message": str(exc)[:2000]},
                "duration_ms": 0,
            }
        )
