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

    # ── LLM router tuning ──
    llm_routes_path: str = Field(default="config/llm_routes.toml")
    circuit_failure_threshold: int = Field(default=4)
    circuit_reset_seconds: float = Field(default=60.0)
    llm_schema_retries: int = Field(default=1)

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
