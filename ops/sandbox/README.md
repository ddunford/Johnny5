# code_exec sandbox (Phase 6b, TASK-6b.6a)

The hardened container that runs Johnny's `code_exec` Python snippets. **Assume
the code is hostile** — the container is the *only* trust boundary (we do not
sandbox Python at the language level; that's bypassable and gives false
confidence). The `code_exec` tool (TASK-6b.6b, fastapi) dispatches snippets in
and reads a verdict out via the launcher below.

## Files

| File | Owner | Purpose |
|------|-------|---------|
| `Dockerfile` | devops | The minimal `johnny5-sandbox` image: `python:3.12-slim`, non-root, no project code, no secrets, only `runner.py`. |
| `runner.py` | devops | In-container entrypoint. Reads code from `SANDBOX_CODE_B64` (env, base64; stdin fallback), runs it, emits one JSON verdict. Captures stdout/stderr at the fd level, bounds output, enforces an in-container timeout, always emits a well-formed verdict. |
| `run-sandbox.sh` | devops | **The security boundary.** Wraps `docker run` with every hardening flag. The tool calls THIS — it never issues `docker` itself. |

Build / verify with the control script:

```bash
./ctl.sh sandbox-build     # docker build -t johnny5-sandbox:latest ops/sandbox
./ctl.sh sandbox-test      # the escape battery — must be 9/9 before shipping code_exec
```

`./ctl.sh up` also builds the image if missing.

## Invocation contract (what the `code_exec` tool calls)

```
ops/sandbox/run-sandbox.sh [timeout_seconds]
  stdin  : UTF-8 Python source (the snippet)
  stdout : exactly ONE line of JSON (the verdict — see below)
  stderr : human diagnostics only; do NOT parse
  exit   : 0  when a sandbox verdict was produced — success, user error,
              timeout, OR resource-kill are ALL exit 0 with a JSON verdict.
           3  launcher INFRA failure (docker/image unavailable) — still emits a
              JSON verdict on stdout (error.type = "SandboxUnavailable").
```

Per-call tunables via env (all optional, sane defaults):

| Env | Default | Meaning |
|-----|---------|---------|
| `SANDBOX_IMAGE` | `johnny5-sandbox:latest` | image tag |
| `SANDBOX_TIMEOUT` (or `$1`) | `10` | in-container graceful timeout (s) |
| `SANDBOX_GRACE` | `5` | extra seconds before the launcher's hard SIGKILL backstop |
| `SANDBOX_MEMORY` | `256m` | hard memory cap (OOM-killed past it) |
| `SANDBOX_CPUS` | `1.0` | CPU cap |
| `SANDBOX_PIDS` | `128` | max processes/threads (fork-bomb cap) |
| `SANDBOX_OUTPUT_MAX` | `65536` | per-stream output byte cap before truncation |

### Verdict shape (stable — pin a contract test against this)

```jsonc
{
  "ok": true,            // false on user exception, timeout, or resource-kill
  "timed_out": false,    // true ONLY for a time-limit kill (not OOM/resource)
  "exit_code": 0,        // 0 ok; 1 user exception; 124 timeout; 137 SIGKILL/OOM
  "stdout": "hello\n",   // captured stdout (truncated to SANDBOX_OUTPUT_MAX)
  "stderr": "",          // captured stderr / traceback (truncated)
  "truncated": false,    // true if stdout or stderr was clipped
  "error": null,         // {"type": "...", "message": "..."} on failure, else null
  "duration_ms": 0       // user-code wall time (graceful path) or container wall time (kill path)
}
```

Observed examples:

```jsonc
// success
{"ok": true,  "timed_out": false, "exit_code": 0,   "stdout": "hello\n", "stderr": "warn\n", "truncated": false, "error": null, "duration_ms": 0}
// user exception
{"ok": false, "timed_out": false, "exit_code": 1,   "stdout": "", "stderr": "Traceback ...\nValueError: boom\n", "truncated": false, "error": {"type": "ValueError", "message": "boom"}, "duration_ms": 51}
// timeout (in-container guard fired)
{"ok": false, "timed_out": true,  "exit_code": 124, "stdout": "", "stderr": "", "truncated": false, "error": {"type": "Timeout", "message": "exceeded 3s of CPU/wall time"}, "duration_ms": 2580}
// memory cap / resource kill (kernel OOM-killer)
{"ok": false, "timed_out": false, "exit_code": 137, "stdout": "", "stderr": "killed by SIGKILL — memory (OOM) or resource cap", "error": {"type": "Killed", "message": "sandbox terminated by the kernel resource guard (memory/pids/cpu cap)"}, "duration_ms": 1015}
```

The tool should map `ok`/`error`/`timed_out` onto its typed `ToolResult` and may
enforce its own outer timeout as a belt-and-suspenders (the launcher's backstop
is `SANDBOX_TIMEOUT + SANDBOX_GRACE`; set the tool's subprocess timeout a little
higher, e.g. `+3s`, so the launcher always wins and returns structured JSON
rather than the tool killing it).

## The boundary (what `run-sandbox.sh` enforces)

- `--network none` — no egress at all (no SSRF, no exfiltration, no `pip install`)
- `--read-only` rootfs + a single `--tmpfs /tmp` mounted `noexec,nosuid,nodev,size=16m`
- `--user 65534:65534` (nobody) + `--cap-drop ALL` + `--security-opt no-new-privileges`
- `--memory` / `--memory-swap` (no swap) / `--cpus` / `--pids-limit` resource caps
- `--ipc none`, `--cgroupns private`
- **no host bind mounts** — the container can't see the repo, `.env`, or `data/`
- hard wall-clock SIGKILL backstop via `timeout`; per-container `--rm` + a launcher
  trap so a killed launcher never leaks a sandbox container
- defence-in-depth inside the runner: `RLIMIT_CPU`/`NPROC`/`FSIZE`/`CORE` + a
  SIGALRM/SIGXCPU timeout

The escape battery (`./ctl.sh sandbox-test`) proves: compute works; runs
non-root; sees only the container fs (not the host); rootfs read-only; `/tmp`
writable; network cut; infinite loop killed; memory cap OOM-kills; thread/pid
fan-out capped.

## How the api reaches Docker — the scoped socket-proxy

The api **never holds the raw `/var/run/docker.sock`**. It talks to a scoped
`docker-socket-proxy` sidecar (`docker-compose.yml` service `docker-proxy`) via
`DOCKER_HOST=tcp://docker-proxy:2375`. The api image carries only the Docker
**client** binary (copied from `docker:27-cli` in the `Dockerfile`) — no daemon,
no socket mount.

The proxy allowlist is the minimum `run-sandbox.sh` needs and nothing else:

| Allowed | Why |
|---------|-----|
| `CONTAINERS=1` + `POST=1` | create / start / wait / logs / rm |
| `IMAGES=1` | image inspect (presence check; never pulls) |
| `PING=1`, `VERSION=1` | CLI connect + API-version negotiation |

Everything else is **denied**: `EXEC`, `VOLUMES`, `NETWORKS`, `BUILD`, `COMMIT`,
`INFO`, `SYSTEM`, `SWARM`, `SECRETS`, `CONFIGS`, `DISTRIBUTION`, `SESSION`, `AUTH`,
… (all `0`). The launcher runs the container **detached** and feeds the snippet
via `SANDBOX_CODE_B64`, so it never needs the attach/exec endpoints. Verified:
through the proxy, `docker exec`/`info`/`volume ls`/`network ls` all 403 while the
sandbox flow + all hardening (`--network none`, memory OOM, non-root) hold.

### Residual risk + upgrade path (for the 6b.13 review)

Even scoped, `/containers/create` lets a **compromised api** craft a
privileged/host-mounted container → host escape. This is **accepted** for a
single-user LAN research box with the proxy in place (it removes the much larger
"raw socket = full daemon control" surface). The hardening that contains *hostile
sandboxed code* is unaffected — that code has no socket and no network.

**Upgrade path if Johnny ever goes internet-facing:** replace the proxy with a
**sandbox-runner sidecar** that exposes only `run(snippet) -> verdict` and bakes
the hardening flags in itself, so the api can no longer issue arbitrary
`/containers/create` at all. The image + `runner.py` + the stdin→JSON contract
above stay identical; only the launcher's transport changes.
