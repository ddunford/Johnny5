"""Deterministic monotonic clock for tests.

The LLM router's circuit breaker resets after a cooldown (~60s). Testing that
"after the cooldown the circuit half-opens and recovers" must not depend on a
real ``sleep`` — it has to be exact and instant. ``FrozenClock`` is a drop-in
replacement for ``time.monotonic`` (a zero-arg callable returning float
seconds) that only moves when the test advances it.

Intended use (see also the message to the backend teammate): the circuit
breaker should accept an injectable monotonic clock, e.g.::

    CircuitBreaker(..., clock: Callable[[], float] = time.monotonic)

Then a resilience test does::

    clock = FrozenClock()
    breaker = CircuitBreaker(clock=clock)
    # ... trip it ...
    clock.advance(60.0)   # cooldown elapses, instantly and exactly
    # ... assert half-open / recovery ...
"""

from __future__ import annotations


class FrozenClock:
    """A monotonic-style clock whose value only changes via ``advance``/``set``.

    Callable like ``time.monotonic``: ``clock()`` returns the current value in
    float seconds. Monotonic invariant enforced — it never moves backwards.
    """

    def __init__(self, start: float = 0.0) -> None:
        self._now = float(start)

    def __call__(self) -> float:
        """Return the current time in seconds (``time.monotonic`` shape)."""
        return self._now

    # Alias so the instance can also stand in for the ``time`` module's
    # ``.monotonic`` attribute if a caller injects a module-like object.
    def monotonic(self) -> float:
        return self._now

    def advance(self, seconds: float) -> float:
        """Move the clock forward by ``seconds`` and return the new value."""
        if seconds < 0:
            raise ValueError("a monotonic clock cannot move backwards")
        self._now += float(seconds)
        return self._now

    def set(self, value: float) -> None:
        """Set the clock to an absolute value (must not move backwards)."""
        value = float(value)
        if value < self._now:
            raise ValueError("a monotonic clock cannot move backwards")
        self._now = value
