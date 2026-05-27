"""Application settings, loaded from the environment / `.env`.

All configuration is read here via pydantic-settings. Secrets (DB password, Groq
key, kill-switch token) live only in `.env` (gitignored) — never in code or
committed files. Forward-phase variables (voice, messaging) are present in
`.env.example` but only declared here as code starts to use them; unknown keys
are ignored so an early-phase boot never fails on a not-yet-wired variable.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Runtime ──
    app_env: str = Field(default="development")
    log_level: str = Field(default="INFO")
    service_name: str = Field(default="johnny5")
    sentry_dsn: str = Field(default="")

    # ── Datastores ──
    database_url: str = Field(default="")
    postgres_host: str = Field(default="postgres")
    postgres_port: int = Field(default=5432)
    postgres_db: str = Field(default="johnny5")
    postgres_user: str = Field(default="johnny5")
    postgres_password: str = Field(default="")
    redis_url: str = Field(default="redis://redis:6379/0")

    # ── Interface auth (interim gate; Phase 5 replaces with full session auth) ──
    # Shared token required to stream /ws/consciousness and to drive the cycle
    # control channel. Empty = open (dev convenience); set a real value in .env so
    # Johnny's inner monologue isn't world-readable on the public router.
    ws_token: str = Field(default="")

    # ── Local inference (Ollama on inference.lan) ──
    local_llm_base_url: str = Field(default="http://inference.lan:8000")
    local_fast_model: str = Field(default="gemma4:e4b")
    local_reasoning_model: str = Field(default="qwen3.5-9b-128k:latest")
    local_llm_timeout: float = Field(default=120.0)

    # ── Embeddings (custom Flask server, /embed contract) ──
    embed_base_url: str = Field(default="http://inference.lan:8002")
    embed_endpoint: str = Field(default="/embed")
    embed_model: str = Field(default="bge-m3")
    embed_dimensions: int = Field(default=1024)

    # ── Vision / detection ──
    yolo_base_url: str = Field(default="http://inference.lan:8003")

    # ── Groq (cloud heavy reasoning) ──
    groq_api_key: str = Field(default="")
    groq_base_url: str = Field(default="https://api.groq.com/openai/v1")
    groq_model: str = Field(default="llama-3.3-70b-versatile")
    groq_daily_budget_usd: float = Field(default=5.00)

    # ── Memory: hybrid recall (similarity × recency × salience) ──
    # Weights for the episodic recall blend. Defaults are equal; Phase 3 Affect
    # and Phase 4 reflection tune these at runtime, so they are config not code.
    memory_recall_weight_similarity: float = Field(default=1.0)
    memory_recall_weight_recency: float = Field(default=1.0)
    memory_recall_weight_salience: float = Field(default=1.0)
    # Recency half-life: an episode this many seconds old scores 0.5 on recency.
    memory_recall_recency_halflife_seconds: float = Field(default=86400.0)
    # How many nearest-by-similarity candidates to pull before re-ranking by the
    # full blend (the ANN step is the similarity gate; re-rank adds recency/salience).
    memory_recall_candidate_pool: int = Field(default=50)

    # ── Memory: working set (Redis, bounded + decaying) ──
    working_memory_capacity: int = Field(default=12)
    working_memory_default_ttl_seconds: float = Field(default=900.0)
    # Multiplicative salience decay applied per ``decay()`` sweep.
    working_memory_decay_factor: float = Field(default=0.9)
    # Items whose salience falls below this after decay are evicted.
    working_memory_salience_floor: float = Field(default=0.05)

    # ── Memory: snapshots (continuity / backups, gitignored) ──
    memory_snapshot_dir: str = Field(default="snapshots/memory")

    # ── LLM router tuning ──
    llm_routes_path: str = Field(default="config/llm_routes.toml")
    circuit_failure_threshold: int = Field(default=4)
    circuit_reset_seconds: float = Field(default=60.0)
    llm_schema_retries: int = Field(default=1)

    # ── Drives (homeostatic motivational core, SPEC §6) ──
    # Per-drive setpoints/rates/thresholds + the event→satisfaction map live in
    # this git-backed TOML, not in constants — they are exactly what Johnny
    # re-tunes about himself at runtime (FC-3 / Phase 9). The engine re-syncs the
    # drive_state parameter columns from here on boot.
    drives_config_path: str = Field(default="config/drives.toml")

    # ── Cognitive cycle (the heartbeat) ──
    # Base seconds between ticks. Phase 3 Affect modulates the rate around this
    # (excited → faster, tired → slower), which is why it's config not code.
    cycle_base_interval_seconds: float = Field(default=4.0)
    # Hard bounds on the modulated interval. The floor is a safety bound (a 3.12
    # concern): no amount of arousal may shorten the tick below this, so high
    # arousal can't spin the loop and blow the Groq budget. The ceiling caps how
    # slow a tired/calm Johnny ticks (he idles, he doesn't freeze).
    cycle_min_interval_seconds: float = Field(default=1.5)
    cycle_max_interval_seconds: float = Field(default=12.0)
    # Weight of arousal on rate: interval ∝ 1/(1 + speedup·arousal). Higher = a
    # given arousal speeds the heartbeat more (still clamped to the floor).
    cycle_arousal_speedup: float = Field(default=1.0)
    # Extra slowdown as the Energy drive (tiredness) exceeds its threshold —
    # interval ∝ (1 + slowdown·energy_excess): the heartbeat drags toward sleep.
    cycle_tired_slowdown: float = Field(default=1.5)
    # How many salient items Attention may place on the workspace per tick — the
    # bottleneck bound (LIDA/GWT: a wider workspace degrades, not improves).
    workspace_capacity: int = Field(default=7)

    # ── Sensorium (perception) ──
    # An incoming message is a high-salience interrupt that wins attention; the
    # ambient system-metrics percept sits low so it only surfaces when idle (which
    # is what makes the "need input" character beat emerge). Tunable (FC-3).
    sensorium_input_salience: float = Field(default=0.85)
    sensorium_ambient_salience: float = Field(default=0.15)
    # The ambient system percept is sampled every tick (so the workspace is never
    # empty) but only *persisted* every N ticks, so an idle Johnny doesn't flood
    # the percept log with "nothing happened" rows (Phase 4 sleep prunes the rest).
    sensorium_ambient_persist_every_ticks: int = Field(default=15)

    # ── Attention (the bottleneck) ──
    # Salience = intrinsic weight + a novelty bonus; recently-surfaced content is
    # penalised so Johnny doesn't fixate on the same ambient line every tick.
    # Phase 3 adds goal/drive/emotional-charge terms. Tunable (FC-3).
    attention_weight_salience: float = Field(default=1.0)
    attention_weight_novelty: float = Field(default=0.6)
    attention_repeat_penalty: float = Field(default=0.5)
    # Phase-3 affect bias (the FC-7 _score slot): high arousal *narrows* focus —
    # it amplifies already-salient items and dampens marginal ones (SPEC §6.2).
    attention_weight_arousal: float = Field(default=0.7)
    # A drive over threshold boosts the percept kinds relevant to satisfying it
    # (Connection→input, Curiosity→recalled memory/facts), scaled by its urgency.
    attention_weight_drive: float = Field(default=0.5)

    # ── Inner Narrator ──
    # Token ceiling for one thought. Must cover gemma4's reasoning preamble (it
    # behaves like a thinking model under the reflective persona + json_object)
    # PLUS the JSON thought — a low ceiling lets the preamble eat the budget,
    # truncating before any content (the lessons.md trap; qa verified ~800 is the
    # floor, 1024 leaves headroom). Tunable (FC-3).
    narrator_max_tokens: int = Field(default=1024)

    # ── Affect (appraisal → mood, SPEC §6.2) ──
    # Token ceiling for an LLM appraisal of a significant event. gemma4 emits a
    # reasoning preamble before the JSON (the lessons.md trap), so this must clear
    # the preamble + the small appraisal object — 512 leaves headroom. Tunable (FC-3).
    affect_max_tokens: int = Field(default=512)
    # A feeling fades: mood deviation from the calm baseline halves every this many
    # seconds when nothing sustains it (so an excited spike mellows on its own).
    mood_halflife_seconds: float = Field(default=180.0)
    # Fraction of an appraisal's push applied per tick — mood moves, doesn't snap.
    mood_responsiveness: float = Field(default=0.5)
    # A new mood row is written only when |Δvalence|+|Δarousal| clears this (or the
    # emotion set changes), so the mood history records shifts, not every idle tick.
    mood_persist_min_change: float = Field(default=0.04)

    # ── Memory wiring into the cycle (recall + learn stages) ──
    # How many episodes / facts recall pulls into the workspace each tick.
    memory_recall_episodes_k: int = Field(default=3)
    memory_recall_facts_k: int = Field(default=3)
    # Recalled memory is salient enough to surface but capped *below* a fresh
    # message — recall informs the present, it doesn't crowd it out.
    memory_recall_salience_ceiling: float = Field(default=0.7)
    # Affect bias on recall (SPEC §6.2: emotionally-charged episodes recalled more
    # easily): arousal scales up the salience weight in the recall blend, so when
    # Johnny is activated his charged memories surface more readily.
    memory_recall_arousal_salience_gain: float = Field(default=1.0)
    # An interaction is always written to episodic memory; an idle stream-of-
    # consciousness tick is written only every N ticks (so memory grows from
    # what's notable, not from every 4s of "nothing happened").
    memory_learn_idle_every_ticks: int = Field(default=30)

    # ── Interfaces ──
    public_domain: str = Field(default="johnny.demosrv.uk")
    web_port: int = Field(default=80)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def sqlalchemy_url(self) -> str:
        """The async SQLAlchemy DSN.

        Prefers an explicit ``DATABASE_URL`` (normalised to the asyncpg driver);
        otherwise assembles one from the discrete ``POSTGRES_*`` parts so a bare
        compose env still yields a working connection.
        """
        if self.database_url:
            url = self.database_url
            if url.startswith("postgresql://"):
                url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
            return url
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_production(self) -> bool:
        return self.app_env.lower() in {"production", "prod"}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()
