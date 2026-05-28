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
    # Per-tick fast-model (gemma4) timeout — kept tight so a hung local call fails
    # over fast and doesn't stall the heartbeat.
    local_llm_timeout: float = Field(default=120.0)
    # Reasoning-model (qwen3.5) timeout — applied ONLY when a call targets the
    # reasoning model. qwen is a thinking model used for heavy/OFFLINE roles
    # (deliberation fallback + the sleep roles' qwen fallback), where a long
    # generation (~150s with its reasoning preamble) is fine; the per-tick fast model
    # keeps the tight ``local_llm_timeout`` so hot-path failover stays snappy.
    local_reasoning_timeout: float = Field(default=240.0)

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

    # ── Sleep: consolidation (episodic → semantic, the growth engine, SPEC §8) ──
    # How many recent episodes a single sleep's consolidation pass considers.
    consolidation_recent_limit: int = Field(default=80)
    # Cosine-similarity threshold for two episodes to join the same cluster — the
    # gate between "same theme" and "separate experience". Higher = tighter clusters.
    consolidation_cluster_threshold: float = Field(default=0.6)
    # HARD CAP on clusters summarised per sleep = the per-sleep cap on consolidation
    # LLM calls. The cost guard while the cloud-first `consolidation` role is live and
    # the BudgetGovernor isn't yet a hard pre-call gate (Phase 6). Episodes past the
    # cap fold into their nearest existing cluster rather than spawning a new call.
    consolidation_max_clusters: int = Field(default=8)
    # Token ceiling for one cluster summary. consolidation is cloud-first (Groq, no
    # reasoning preamble), but its local fallback is qwen3.5, which spends ~1500
    # tokens on a reasoning preamble BEFORE the JSON — a 1024 ceiling let the preamble
    # eat the whole budget (finish_reason='length', no JSON → silent degrade on every
    # fallback sleep; the lessons.md trap, caught by qa's @live leg). 4096 clears the
    # preamble + the triple with headroom and is a no-op for the Groq primary. (FC-3)
    consolidation_max_tokens: int = Field(default=4096)
    # Default confidence for a consolidated fact when the model doesn't supply one.
    consolidation_default_confidence: float = Field(default=0.6)

    # ── Sleep: memory decay + merge (SPEC §8 — decay ≠ deletion) ──
    # Episodic memory is append-only in v1: decay only LOWERS salience so recall
    # favours what matters; an episode is NEVER hard-deleted. Only semantic facts
    # merge/dedupe (the autobiography must survive).
    # Max episodes a single decay pass re-scores (bounds the sweep).
    decay_episode_limit: int = Field(default=2000)
    # Salience half-life: an (uncharged) episode this many seconds old retains half
    # its above-floor salience. 7 days — recent memories stay vivid, old ones fade.
    decay_salience_halflife_seconds: float = Field(default=604800.0)
    # Salience never decays below this floor (decay ≠ deletion — the row survives).
    decay_salience_floor: float = Field(default=0.05)
    # Emotionally-charged / goal-relevant (consolidated-source) episodes are
    # STRENGTHENED: after decay their salience is pulled this fraction toward 1.0,
    # so the memories that matter resist fading (SPEC §8 "strengthen ... relevant").
    decay_charged_boost: float = Field(default=0.25)
    # Cosine ≥ this ⇒ two semantic facts are near-duplicates and merge into one
    # (provenance unioned, confidence maxed). High, so only true restatements merge.
    semantic_merge_threshold: float = Field(default=0.95)

    # ── Sleep: self-model refresh (the evolving self-concept, SPEC §5 #11) ──
    # How many recent episodes / semantic facts the self-model reflection considers.
    self_model_recent_episodes: int = Field(default=15)
    self_model_recent_facts: int = Field(default=10)
    # Token ceiling for one self-model refresh. self_model is cloud-first (Groq), but
    # the local fallback is qwen3.5 (~1500-token reasoning preamble) and the output is
    # a sizeable JSON object (doc + values + concerns + relationships). 1536 truncated
    # on the qwen fallback (finish_reason='length'; qa's @live leg); 4096 clears the
    # preamble + the object. No-op for the Groq primary path (just a ceiling). (FC-3)
    self_model_max_tokens: int = Field(default=4096)

    # ── Sleep: metacognition review (self-improvement proposals, SPEC §5 #12) ──
    # How many recent goals / degraded-tick thoughts the review inspects.
    metacognition_recent_goals: int = Field(default=20)
    # Token ceiling for one metacognition review (first-person review + a few
    # proposals as JSON). Same qwen reasoning-preamble headroom rationale as the other
    # two sleep roles — 1536 truncated on the qwen fallback (qa's @live leg). (FC-3)
    metacognition_max_tokens: int = Field(default=4096)

    # ── Sleep cycle (the offline phase the run loop enters between ticks) ──
    # Optional fallback cadence: force a sleep every N ticks even if Energy hasn't
    # crossed threshold. 0 = disabled — Energy (the is_sleep_signal urge) is the real
    # trigger (its equilibrium ~0.87 sits above its 0.80 threshold, so it always
    # crosses while awake). A small value is handy for demos/tests. Tunable (FC-3).
    sleep_every_ticks: int = Field(default=0)

    # ── LLM router tuning ──
    llm_routes_path: str = Field(default="config/llm_routes.toml")
    circuit_failure_threshold: int = Field(default=4)
    circuit_reset_seconds: float = Field(default=60.0)
    llm_schema_retries: int = Field(default=1)

    # ── Deliberation (goal → internal action; the autonomy loop's act step) ──
    # Deliberation is a heavy agent (SPEC §7: heavy modules fire on a slower
    # sub-cadence to control cost). It *acts* on the active goal at most this often,
    # so a pegged drive can't drive an action (and its LLM call) every single tick —
    # the goal/action loop stays bounded (a 3.12 concern). Arbitration still runs
    # every tick (cheap); only the action is throttled.
    deliberation_min_interval_seconds: float = Field(default=20.0)
    # Token ceiling for an internal reflection. Free-text (no json_object), so
    # gemma4 returns clean prose without the reasoning-preamble trap.
    deliberation_max_tokens: int = Field(default=320)
    # How many memories a recall/consolidate action surfaces.
    deliberation_recall_k: int = Field(default=4)
    # Token ceiling when Deliberation formulates a code_exec snippet for a Mastery
    # goal (free-text Python, no schema). Generous enough for a short snippet; the
    # tool's char cap (code_exec_max_code_chars) is the hard bound on what runs.
    mastery_code_max_tokens: int = Field(default=600)

    # ── Conscience (CHECK stage — Johnny's values vetting an action, SPEC §5 #14) ──
    # The Conscience judges a proposed (tool, args) against Johnny's *values only*
    # (FC-9) on the local/fast `conscience` role. It asks for a small JSON verdict
    # ({"verdict","reason"}); like the other local schema roles, the fast model can
    # emit a short reasoning preamble before the JSON, so the ceiling must clear the
    # preamble + the verdict object (the lessons.md token-trap). Tunable (FC-3).
    conscience_max_tokens: int = Field(default=512)

    # ── Goal arbitration (urge → goal, with anti-thrash hysteresis) ──
    # A promoted goal is held for at least this long before any competing urge can
    # displace it — the anti-thrash guard so Johnny doesn't flip-flop between
    # curiosity and connection every tick (TC-3.6).
    goal_min_dwell_seconds: float = Field(default=45.0)
    # After the dwell, a challenger from a *different* drive must beat the
    # incumbent's current priority by at least this margin to take over.
    goal_hysteresis_margin: float = Field(default=0.15)
    # Affect weighting on urge priority: priority = urgency·(1 + weight·arousal),
    # so an activated Johnny pursues his strongest need more decisively.
    goal_arousal_weight: float = Field(default=0.5)

    # ── Drives (homeostatic motivational core, SPEC §6) ──
    # Per-drive setpoints/rates/thresholds + the event→satisfaction map live in
    # this git-backed TOML, not in constants — they are exactly what Johnny
    # re-tunes about himself at runtime (FC-3 / Phase 9). The engine re-syncs the
    # drive_state parameter columns from here on boot.
    drives_config_path: str = Field(default="config/drives.toml")

    # ── Cognitive cycle (the heartbeat) ──
    # Whether the heartbeat auto-starts on boot. Default true (he's alive the
    # moment the process is up). When false, the bus + control + API + WS all run
    # but the loop never ticks — a frozen "maintenance"/observation mode that
    # holds a deterministic empty state (no thoughts/mood/sleep), used by the
    # fresh-load smoke + demos. He can still be stepped via the control channel.
    cycle_autostart: bool = Field(default=True)
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
    # the preamble (~250) + the appraisal object. A live regression guard proved
    # 512 gets eaten → empty content → silent appraisal failure; 1024 matches the
    # narrator's proven headroom. Tunable (FC-3).
    affect_max_tokens: int = Field(default=1024)
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

    # ── Web API (the /api/v1 read/input doorway, Phase 5a) ──
    # Max characters one /api/v1/input message may carry — a longer body is rejected
    # (413) so the percept queue / episodic memory can't be flooded with a giant blob.
    web_input_max_chars: int = Field(default=4000)
    # Back-pressure on /api/v1/input: when the shared Sensorium InputQueue already
    # holds this many undrained inputs, further sends are rejected (429) so a runaway
    # client/loop can't grow the Redis list unboundedly faster than the cycle drains it.
    web_input_max_queue_depth: int = Field(default=100)

    # ── web_fetch tool (the SSRF-hardened reader, Phase 6b — SPEC §9.1) ──
    # Johnny picks URLs autonomously, so web_fetch is the phase's #1 SSRF surface.
    # These are *Core-style caps*, not caller args: a tool's args can't loosen them
    # (raising the size cap or following more redirects must not be Johnny's choice).
    # The IP deny-list + scheme allowlist live in code (brain/effectors/safe_http.py).
    web_fetch_user_agent: str = Field(
        default="Johnny5/1.0 (+autonomous reader; https://johnny.demosrv.uk)"
    )
    # Max redirect hops to follow. Each hop is re-resolved + re-validated against the
    # IP deny-list BEFORE connecting, so a public URL can't 302 its way to an internal host.
    web_fetch_max_redirects: int = Field(default=5)
    # Per-request wall-clock budget (connect + read). A slow/hostile server is aborted.
    web_fetch_timeout_seconds: float = Field(default=10.0)
    # Hard cap on raw bytes downloaded — the stream is aborted past this (a huge/inifinite
    # body can't exhaust memory). Reads are streamed + counted, never buffered blind.
    web_fetch_max_bytes: int = Field(default=2_000_000)
    # Cap on the *extracted* text returned to the workspace. Attention is a bottleneck
    # (SPEC §5): a fetched page is distilled, never dumped whole into Johnny's context.
    web_fetch_max_text_chars: int = Field(default=20_000)

    # ── code_exec tool (the sandboxed Python executor, Phase 6b — SPEC §9.1/§9.3) ──
    # The tool dispatches a snippet INTO the hardened johnny5-sandbox container via
    # ops/sandbox/run-sandbox.sh — the security boundary (never exec on the host). All
    # the container isolation flags live in that launcher; these are the tool-side knobs.
    sandbox_launcher_path: str = Field(default="ops/sandbox/run-sandbox.sh")
    # In-container graceful timeout (s), passed to the launcher ($1 / SANDBOX_RUN_TIMEOUT).
    code_exec_timeout_seconds: int = Field(default=10)
    # The tool waits ``timeout + this`` for the launcher subprocess before killing it.
    # Must exceed the launcher's own hard backstop margin (SANDBOX_GRACE, default 5s) so
    # the launcher always wins and returns a structured verdict rather than the tool
    # killing it mid-run (README guidance: a few seconds over the backstop).
    code_exec_outer_buffer_seconds: float = Field(default=8.0)
    # Arg-level cap on snippet size — a huge blob is rejected before it reaches the sandbox.
    code_exec_max_code_chars: int = Field(default=50_000)

    # ── web_search / news (SearXNG on inference.lan, Phase 6b — his discovery surface) ──
    searxng_url: str = Field(default="http://inference.lan:8889")
    # The engine set passed on every query. VERIFIED on the box: google/bing/brave work;
    # duckduckgo returns 0 results and an empty engine set times out — so this is NOT
    # optional tuning, it's the working contract. Comma-separated (SearXNG's `engines=`).
    searxng_engines: str = Field(default="google,bing,brave")
    # SearXNG fans out to upstream engines, so allow a generous per-query budget.
    searxng_timeout_seconds: float = Field(default=15.0)
    # Cap how many results a search/news tool surfaces (Attention bottleneck, SPEC §5).
    web_search_max_results: int = Field(default=10)
    news_max_results: int = Field(default=10)

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
