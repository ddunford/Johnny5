"""Self-tests for the FrozenClock test helper."""

from __future__ import annotations

import pytest

from helpers.clock import FrozenClock


def test_starts_at_zero_by_default() -> None:
    clock = FrozenClock()
    assert clock() == 0.0
    assert clock.monotonic() == 0.0


def test_starts_at_given_value() -> None:
    clock = FrozenClock(start=100.0)
    assert clock() == 100.0


def test_advance_moves_forward_and_returns_new_value() -> None:
    clock = FrozenClock()
    assert clock.advance(60.0) == 60.0
    assert clock() == 60.0
    clock.advance(0.5)
    assert clock() == 60.5


def test_callable_reflects_advances_for_injected_use() -> None:
    # Simulates injection as ``clock: Callable[[], float]`` into a breaker.
    clock = FrozenClock()
    readings = [clock()]
    clock.advance(30.0)
    readings.append(clock())
    clock.advance(30.0)
    readings.append(clock())
    assert readings == [0.0, 30.0, 60.0]


def test_set_absolute_value() -> None:
    clock = FrozenClock()
    clock.set(123.0)
    assert clock() == 123.0


def test_advance_rejects_negative() -> None:
    clock = FrozenClock(start=10.0)
    with pytest.raises(ValueError):
        clock.advance(-1.0)
    assert clock() == 10.0


def test_set_rejects_backwards() -> None:
    clock = FrozenClock(start=10.0)
    with pytest.raises(ValueError):
        clock.set(5.0)
    assert clock() == 10.0
