"""Per-provider circuit breaker.

Opens after N consecutive transport failures and stays open for a cooldown, then
allows a single trial (half-open). A success closes it; a failure re-opens it.
The clock is injectable so the cognitive cycle's frozen-clock test harness can
drive open→half-open→closed transitions deterministically.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from enum import StrEnum


class CircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    def __init__(
        self,
        *,
        failure_threshold: int = 4,
        reset_timeout: float = 60.0,
        time_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self._failure_threshold = failure_threshold
        self._reset_timeout = reset_timeout
        self._time_fn = time_fn
        self._failures = 0
        self._opened_at: float | None = None
        self._state = CircuitState.CLOSED

    @property
    def state(self) -> CircuitState:
        # Recompute lazily so an OPEN circuit becomes HALF_OPEN once cooled down,
        # even if no one explicitly polled in between.
        if self._state is CircuitState.OPEN and self._cooldown_elapsed():
            self._state = CircuitState.HALF_OPEN
        return self._state

    def allow(self) -> bool:
        """Whether a call may proceed now. Transitions OPEN→HALF_OPEN on cooldown."""
        return self.state in (CircuitState.CLOSED, CircuitState.HALF_OPEN)

    def record_success(self) -> None:
        self._failures = 0
        self._opened_at = None
        self._state = CircuitState.CLOSED

    def record_failure(self) -> None:
        self._failures += 1
        # A failure during a half-open trial (or once the threshold is hit) opens
        # the circuit and restarts the cooldown.
        if self._state is CircuitState.HALF_OPEN or self._failures >= self._failure_threshold:
            self._state = CircuitState.OPEN
            self._opened_at = self._time_fn()

    def _cooldown_elapsed(self) -> bool:
        if self._opened_at is None:
            return False
        return (self._time_fn() - self._opened_at) >= self._reset_timeout
