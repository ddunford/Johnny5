"""Phase-4 table groups for clean-schema fixtures (self-model + metacognition + sleep).

Mirrors the ``drives_db`` / ``memory_db`` pattern (tests/drives/conftest.py,
tests/memory/conftest.py): a DB-backed Phase-4 suite installs a fresh loop-local
global engine (helpers.db) and ``TRUNCATE``s **only** the tables its tests touch,
so each test starts and ends on a clean slate. The table lists are centralised
here so they stay single-sourced across the sleep / self-model / metacognition /
memory-growth suites instead of drifting copy-paste tuples in four conftests.

Truncation is children-first; ``TRUNCATE ... RESTART IDENTITY CASCADE`` (see
``helpers.db.truncate_tables``) covers the FK edges — ``thought.mood_id`` → ``mood``
and ``semantic_edge`` → ``semantic_fact``. The Phase-4 growth tables (``identity``,
``self_improvement_note``, ``sleep_log``) are append-versioned / log rows with no
inbound FKs in v1, so their order among themselves is free; they're listed first.

⚠ ``identity`` is **seeded v1 from the Core anchor** by the migration. ``RESTART
IDENTITY CASCADE`` wipes that seed, so a suite asserting self-model *versioning*
(TC-4.4: refresh → version = previous + 1) must re-establish the v1 baseline after
truncation — either via the backend's idempotent identity bootstrap/seed call (à la
``DriveEngine.bootstrap``) or by seeding a v1 row in the test. The exact seam is a
HOLD item pending backend's SelfModel API; until confirmed, suites that need the
anchor baseline should call it explicitly rather than relying on the migration seed
surviving truncation.
"""

from __future__ import annotations

# ── Phase-4 growth tables (append-versioned / log; no inbound FKs in v1) ─────────
IDENTITY = "identity"
SELF_IMPROVEMENT_NOTE = "self_improvement_note"
SLEEP_LOG = "sleep_log"

_GROWTH = (SLEEP_LOG, SELF_IMPROVEMENT_NOTE, IDENTITY)

# ── inherited subsystems a Phase-4 run reads / writes (children-first) ───────────
# Phase-2 heartbeat (a sleep-in-the-loop test writes thoughts/percepts/events).
_HEARTBEAT = ("thought", "percept", "workspace_event")
# Phase-3 motivational state (energy restore, mood, goals resolved/abandoned).
_MOTIVATION = ("goal", "mood", "drive_state")
# Phase-1 memory spine (consolidation reads episode → writes semantic_fact;
# decay/merge touches episode + semantic_fact; edges reference facts).
_MEMORY = ("semantic_edge", "semantic_fact", "skill", "episode")

# ── per-suite truncation scopes (children-first, FK-safe) ────────────────────────

#: Everything a full sleep run can touch — the offline pipeline (consolidate →
#: decay/merge → self-model refresh → metacognition → snapshot → restore-energy →
#: wake) plus the heartbeat it pauses. Use for the sleep-cycle + snapshot-v2
#: round-trip + continuity + wake-self-check suites.
SLEEP_TABLES = (*_GROWTH, *_HEARTBEAT, *_MOTIVATION, *_MEMORY)

#: Self-model refresh: writes ``identity``; reads recent episodes/facts + drive/mood
#: history. Scoped to those, not the whole heartbeat.
SELF_MODEL_TABLES = (
    IDENTITY,
    "mood",
    "drive_state",
    "semantic_edge",
    "semantic_fact",
    "episode",
)

#: Metacognition review: writes ``self_improvement_note``; reads goal outcomes
#: (resolved vs abandoned), drive/mood patterns, and degraded ticks (``thought``).
METACOGNITION_TABLES = (
    SELF_IMPROVEMENT_NOTE,
    "goal",
    "mood",
    "drive_state",
    "thought",
)

#: Consolidation + decay/merge: the memory spine only (episode ↔ semantic_fact).
#: Identical scope to the existing ``memory_db`` fixture — reuse that where a test
#: already lives under tests/memory/; this alias documents the Phase-4 intent.
MEMORY_GROWTH_TABLES = ("semantic_edge", "semantic_fact", "episode")

__all__ = [
    "IDENTITY",
    "MEMORY_GROWTH_TABLES",
    "METACOGNITION_TABLES",
    "SELF_IMPROVEMENT_NOTE",
    "SELF_MODEL_TABLES",
    "SLEEP_LOG",
    "SLEEP_TABLES",
]
