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

from redis.asyncio import Redis

from brain.cycle import CognitiveCycle
from foundation.observability import get_logger
from foundation.redis_client import get_redis

_log = get_logger("brain.cycle_control")

CONTROL_CHANNEL = "johnny:cycle:control"

PAUSE = "pause"
RESUME = "resume"
STEP = "step"
_COMMANDS = frozenset({PAUSE, RESUME, STEP})


async def send_control(
    command: str, *, redis: Redis | None = None, channel: str = CONTROL_CHANNEL
) -> None:
    """Publish a control command (REPL side). Unknown commands are rejected here."""
    if command not in _COMMANDS:
        raise ValueError(f"unknown control command {command!r} (want one of {sorted(_COMMANDS)})")
    client = redis or get_redis()
    await client.publish(channel, json.dumps({"command": command}))


class CycleControlListener:
    """Runtime side: apply Redis control commands to the in-process cycle gate."""

    def __init__(
        self,
        cycle: CognitiveCycle,
        *,
        redis: Redis | None = None,
        channel: str = CONTROL_CHANNEL,
    ) -> None:
        self._cycle = cycle
        self._redis = redis or get_redis()
        self._channel = channel
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
        try:
            command = json.loads(data).get("command") if isinstance(data, str | bytes) else None
        except (ValueError, TypeError):
            _log.warning("cycle_control.malformed_command")
            return
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
