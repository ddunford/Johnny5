"""Motivational core: drive_state, mood, goal — the autonomy substrate.

Phase 3 (``SPEC §6``) gives Johnny something he *wants*. Three tables:

* ``drive_state`` — the seven homeostatic drives. Each is a value in ``[0,1]``
  (the **pressure / urgency**, not a resource level) that drifts away from its
  ``setpoint`` over *time* (``accrual_rate``) and relaxes back on satisfaction
  (``decay_rate`` + outcome events). When ``value`` crosses ``threshold`` the
  drive emits an Urge. This is rate-based homeostasis — the value builds while
  idle, which is what makes the "need input" beat emerge with no input at all.
  ``updated_at`` is the last advance instant, so elapsed-time accrual is exact
  and survives a restart (FC-6 continuity: drives don't reset to zero on reboot).
  Seven rows are seeded at rest; ``config/drives.toml`` is the runtime-editable
  source of truth for the parameters (FC-3) and the engine re-syncs them on boot.

* ``mood`` — the affect history: a continuous ``valence``×``arousal`` point plus
  the discrete ``emotions`` tagged at that instant. Append-only; the latest row
  is the current mood. ``thought.mood_id`` (added FK-less in 0003) now gains its
  FK here, so every narrated thought can be coloured by the mood it was born in.

* ``goal`` — what an urge becomes once the arbiter promotes it. Persists across
  restarts (``status`` + ``plan``/``outcome``) so Johnny resumes a pursuit rather
  than starting blank (``SPEC §6.3``).

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-27
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


# Seven drives seeded at rest (value = setpoint). Parameters mirror
# ``config/drives.toml`` (the FC-3 runtime-editable source of truth); the engine
# re-syncs them from config on boot, so this is the initial-state seed only.
# Rates are per-second; equilibrium ≈ setpoint + accrual_rate/decay_rate (clamped
# to 1.0). Curiosity/Boredom/Connection climb past threshold while idle; Energy
# tiredness climbs with active ticks; Continuity's equilibrium sits *below* its
# threshold so it stays grounded — it only spikes on real shutdown signals, never
# theatrically on its own (the plan's "will to live, kept grounded").
_DRIVE_SEEDS = (
    # drive,        setpoint, accrual_rate, decay_rate, threshold
    ("curiosity", 0.10, 0.0008, 0.0002, 0.65),
    ("boredom", 0.05, 0.0007, 0.0003, 0.70),
    ("connection", 0.10, 0.0005, 0.0001, 0.70),
    ("mastery", 0.15, 0.0003, 0.0002, 0.75),
    ("coherence", 0.10, 0.0002, 0.0002, 0.75),
    ("energy", 0.10, 0.0006, 0.0001, 0.80),
    ("continuity", 0.10, 0.0001, 0.0003, 0.85),
)


def upgrade() -> None:
    # ── drive_state: the seven homeostatic drives (pressure in [0,1]) ────────
    op.create_table(
        "drive_state",
        # The drive name is the natural key — one row per drive, ever.
        sa.Column("drive", sa.String(length=32), nullable=False),
        # Current pressure/urgency in [0,1] (NOT a resource level — high = needs action).
        sa.Column("value", sa.Float(), nullable=False),
        # Resting pressure the drive relaxes toward when fully satisfied.
        sa.Column("setpoint", sa.Float(), nullable=False),
        # Per-second pressure accrual while unmet — the idle build-up ("rises when idle").
        sa.Column("accrual_rate", sa.Float(), nullable=False),
        # Per-second passive relaxation toward setpoint (homeostatic pull-back).
        sa.Column("decay_rate", sa.Float(), nullable=False),
        # Urgency threshold: value >= threshold emits an Urge.
        sa.Column("threshold", sa.Float(), nullable=False),
        # Last advance instant — elapsed-time accrual keys off this, so it is exact
        # and survives a restart (drives don't reset to zero on reboot).
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("drive", name=op.f("pk_drive_state")),
        sa.CheckConstraint("value >= 0 AND value <= 1", name=op.f("ck_drive_state_value_range")),
    )

    drive_state = sa.table(
        "drive_state",
        sa.column("drive", sa.String),
        sa.column("value", sa.Float),
        sa.column("setpoint", sa.Float),
        sa.column("accrual_rate", sa.Float),
        sa.column("decay_rate", sa.Float),
        sa.column("threshold", sa.Float),
    )
    op.bulk_insert(
        drive_state,
        [
            {
                "drive": drive,
                "value": setpoint,  # seed at rest
                "setpoint": setpoint,
                "accrual_rate": accrual_rate,
                "decay_rate": decay_rate,
                "threshold": threshold,
            }
            for (drive, setpoint, accrual_rate, decay_rate, threshold) in _DRIVE_SEEDS
        ],
    )

    # ── mood: the affect history (latest row = current mood) ─────────────────
    op.create_table(
        "mood",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "ts",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        # Pleasantness in [-1, 1]; negative = unpleasant.
        sa.Column("valence", sa.Float(), nullable=False),
        # Activation in [0, 1]; high = excited/anxious, low = calm/tired.
        sa.Column("arousal", sa.Float(), nullable=False),
        # Discrete emotion tags with intensities, e.g. {"curiosity": 0.6}.
        sa.Column("emotions", postgresql.JSONB(), server_default="{}", nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mood")),
        sa.CheckConstraint("valence >= -1 AND valence <= 1", name=op.f("ck_mood_valence_range")),
        sa.CheckConstraint("arousal >= 0 AND arousal <= 1", name=op.f("ck_mood_arousal_range")),
    )
    op.create_index("ix_mood_ts", "mood", ["ts"])

    # ── thought.mood_id: wire the FK 0003 deferred until mood existed ────────
    op.create_foreign_key(
        op.f("fk_thought_mood_id_mood"),
        "thought",
        "mood",
        ["mood_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # ── goal: an urge promoted by the arbiter; persists across restarts ──────
    op.create_table(
        "goal",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        # Where the goal came from: a drive name ("curiosity") or "user".
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        # Arbitration priority (drive intensity × affect weighting at promotion).
        sa.Column("priority", sa.Float(), server_default="0", nullable=False),
        # Lifecycle: active | resolved | abandoned.
        sa.Column("status", sa.String(length=32), server_default="active", nullable=False),
        # Deliberation's chosen plan (e.g. {"action": "reflect", ...}).
        sa.Column("plan", postgresql.JSONB(), server_default="{}", nullable=False),
        # Outcome once resolved (feeds drives, affect, episodic memory).
        sa.Column("outcome", postgresql.JSONB(), server_default="{}", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_goal")),
    )
    op.create_index("ix_goal_status", "goal", ["status"])
    op.create_index("ix_goal_created_at", "goal", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_goal_created_at", table_name="goal")
    op.drop_index("ix_goal_status", table_name="goal")
    op.drop_table("goal")

    op.drop_constraint(op.f("fk_thought_mood_id_mood"), "thought", type_="foreignkey")

    op.drop_index("ix_mood_ts", table_name="mood")
    op.drop_table("mood")

    op.drop_table("drive_state")
