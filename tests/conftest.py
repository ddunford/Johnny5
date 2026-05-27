"""Shared pytest configuration and fixtures for the Johnny 5 test suite.

Runner config (asyncio_mode, testpaths, pythonpath) lives in ``pyproject.toml``
under ``[tool.pytest.ini_options]`` — do NOT add a ``pytest.ini`` (it would
silently shadow that). This file owns test-time behaviour only: markers, the
live-endpoint gate, and shared fixtures.

Markers:
  * ``live``       — talks to real inference.lan + Groq. Deselected by default
                     (CI has no LAN/Groq access); pass ``--run-live`` to enable.
  * ``contract``   — provider/adapter response-envelope projection tests.
  * ``resilience`` — router circuit-breaker / failover behaviour tests.
"""

from __future__ import annotations

import json
import sys
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from redis.asyncio import Redis, from_url
from sqlalchemy.ext.asyncio import AsyncEngine

# Make ``helpers`` importable regardless of pytest's import mode.
_TESTS_DIR = Path(__file__).parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

from helpers.clock import FrozenClock  # noqa: E402
from helpers.db import (  # noqa: E402
    migrate_test_db_to_head,
    require_testing_env,
    restart_global_engine,
)

FIXTURES_DIR = _TESTS_DIR / "fixtures"


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "live: test hits real inference.lan + Groq endpoints; deselected unless --run-live",
    )
    config.addinivalue_line(
        "markers", "contract: provider/adapter response-envelope projection test"
    )
    config.addinivalue_line(
        "markers", "resilience: router circuit-breaker / failover behaviour test"
    )


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-live",
        action="store_true",
        default=False,
        help="run @pytest.mark.live tests against the real inference.lan + Groq endpoints",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--run-live"):
        return
    skip_live = pytest.mark.skip(
        reason="live endpoint test — pass --run-live to run against real inference.lan + Groq"
    )
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)


@pytest.fixture
def frozen_clock() -> FrozenClock:
    """A fresh deterministic monotonic clock starting at t=0."""
    return FrozenClock()


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    """Absolute path to ``tests/fixtures``."""
    return FIXTURES_DIR


@pytest.fixture(scope="session")
def load_fixture() -> Callable[[str], Any]:
    """Load a JSON fixture by path relative to ``tests/fixtures``.

    e.g. ``load_fixture("llm/groq_llama33_completion.json")``.
    """

    def _load(relpath: str) -> Any:
        return json.loads((FIXTURES_DIR / relpath).read_text())

    return _load


# ── shared DB / Redis fixtures (cross-loop safe; see helpers/db.py) ──────────────
# Available to every DB-backed suite (memory spine, cognition heartbeat). They
# only do work when a test requests them, so the pure/contract suites are
# unaffected. All require ``./ctl.sh test`` (in-network Postgres/Redis).


@pytest.fixture(scope="session")
def _migrated_test_db() -> None:
    """Bring the test database to ``head`` exactly once per session."""
    migrate_test_db_to_head()


@pytest_asyncio.fixture
async def redis_client() -> AsyncIterator[Redis]:
    """A fresh, loop-local Redis client over a flushed test DB.

    A per-test client avoids reusing a connection bound to another event loop.
    ``./ctl.sh test`` points ``REDIS_URL`` at db 1; a guard refuses to flush
    outside the testing env, and ``flushdb`` only affects the connected (test) DB.
    """
    settings = require_testing_env()
    client: Redis = from_url(settings.redis_url, decode_responses=True)
    await client.flushdb()
    try:
        yield client
    finally:
        await client.flushdb()
        await client.aclose()


@pytest.fixture
def simulate_restart() -> Callable[[], Awaitable[AsyncEngine]]:
    """An awaitable that tears down and rebuilds the process-global engine —
    the in-process stand-in for ``./ctl.sh down && up`` (restart-persistence)."""
    return restart_global_engine
