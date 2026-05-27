"""Cross-process control of the heartbeat (REPL → running cycle).

The cognitive cycle runs in the headless app process; the REPL cockpit is a
separate process. They can't share the in-process pause/step gate, so control
flows over a dedicated Redis pub/sub channel: the REPL ``send_control``s a command
and the runtime-side ``CycleControlListener`` translates it into a call on the
local cycle's gate. Kept out of ``brain/cycle.py`` so the cycle itself stays free
of Redis and trivially unit-testable.
"""

from __future__ import annotations

import json
import secrets

from redis.asyncio import Redis

from brain.cycle import CognitiveCycle
from foundation.config import get_settings
from foundation.observability import get_logger
from foundation.redis_client import get_redis

_log = get_logger("brain.cycle_control")

CONTROL_CHANNEL = "johnny:cycle:control"

PAUSE = "pause"
RESUME = "resume"
STEP = "step"
_COMMANDS = frozenset({PAUSE, RESUME, STEP})


async def send_control(
    command: str,
    *,
    redis: Redis | None = None,
    channel: str = CONTROL_CHANNEL,
    token: str | None = None,
) -> None:
    """Publish a control command (REPL side). Unknown commands are rejected here.

    The command carries the shared token (``settings.ws_token`` by default) so a
    bus-reachable party can't pause/step Johnny's heartbeat unauthenticated — the
    listener drops commands whose token doesn't match. Blank token = gate off.
    """
    if command not in _COMMANDS:
        raise ValueError(f"unknown control command {command!r} (want one of {sorted(_COMMANDS)})")
    effective_token = token if token is not None else get_settings().ws_token
    client = redis or get_redis()
    payload: dict[str, str] = {"command": command}
    if effective_token:
        payload["token"] = effective_token
    await client.publish(channel, json.dumps(payload))


class CycleControlListener:
    """Runtime side: apply Redis control commands to the in-process cycle gate."""

    def __init__(
        self,
        cycle: CognitiveCycle,
        *,
        redis: Redis | None = None,
        channel: str = CONTROL_CHANNEL,
        token: str | None = None,
    ) -> None:
        self._cycle = cycle
        self._redis = redis or get_redis()
        self._channel = channel
        # Blank token = gate off (dev). Otherwise a command must carry a matching
        # token or it's dropped — nobody pauses his heartbeat unauthenticated.
        self._token = token if token is not None else get_settings().ws_token
        self._running = False

    async def run(self) -> None:
        """Listen for control commands and apply them until ``stop`` is called."""
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(self._channel)
        self._running = True
        try:
            while self._running:
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if message is None:
                    continue
                self._apply(message["data"])
        finally:
            await pubsub.unsubscribe(self._channel)
            await pubsub.aclose()

    def stop(self) -> None:
        self._running = False

    def _apply(self, data: object) -> None:
        if not isinstance(data, str | bytes):
            return
        try:
            message = json.loads(data)
        except (ValueError, TypeError):
            _log.warning("cycle_control.malformed_command")
            return
        if not isinstance(message, dict):
            _log.warning("cycle_control.malformed_command")
            return
        if self._token and not secrets.compare_digest(str(message.get("token", "")), self._token):
            # Unauthenticated control attempt — ignore (token never logged).
            _log.warning("cycle_control.unauthorised")
            return
        command = message.get("command")
        if command == PAUSE:
            self._cycle.pause()
        elif command == RESUME:
            self._cycle.resume()
        elif command == STEP:
            self._cycle.step()
        else:
            _log.warning("cycle_control.unknown_command", command=command)
            return
        _log.info("cycle_control.applied", command=command)
