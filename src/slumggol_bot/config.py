from __future__ import annotations

from functools import cached_property
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"
    host: str = "0.0.0.0"
    port: int = 8000

    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/slumggol"
    redis_url: str = "redis://localhost:6379/0"

    openai_api_key: str = ""
    openai_model: str = "gpt-5.4"
    openai_transcribe_model: str = "gpt-4o-transcribe"

    telegram_base_url: str = "https://api.telegram.org"
    telegram_bot_token: str = ""
    telegram_webhook_secret: str = ""
    telegram_ingest_mode: str = "polling"
    telegram_poll_timeout_seconds: int = 20
    telegram_poll_interval_seconds: float = 1.0
    telegram_poll_limit: int = 50

    enable_clickhouse: bool = True
    clickhouse_url: str = ""
    clickhouse_database: str = "bot_analytics"
    clickhouse_user: str = "default"
    clickhouse_password: str = ""
    clickhouse_async_insert: int = 1
    clickhouse_wait_for_async_insert: int = 1

    analysis_mode: str = "gated"
    demo_mode_max_spend_usd: float = 25.0
    demo_mode_ttl_minutes: int = 120
    hot_claim_min_groups: int = 2
    hot_claim_lookback_minutes: int = 60
    text_simhash_max_distance: int = 3

    reply_confidence_threshold: float = 0.82
    min_sources_required: int = 2

    gpt54_input_cost_per_million: float = Field(default=2.50)
    gpt54_output_cost_per_million: float = Field(default=15.0)
    web_search_cost_per_call: float = Field(default=0.01)
    transcription_cost_per_minute: float = Field(default=0.006)

    @cached_property
    def prompt_path(self) -> Path:
        return Path(__file__).resolve().parent / "prompts" / "factcheck_system.txt"

    @cached_property
    def registry_path(self) -> Path:
        return Path(__file__).resolve().parent / "sources" / "registry.yml"

    def estimate_factcheck_cost(
        self,
        *,
        input_tokens: int,
        output_tokens: int,
        web_search_calls: int = 0,
    ) -> float:
        return (
            (input_tokens / 1_000_000) * self.gpt54_input_cost_per_million
            + (output_tokens / 1_000_000) * self.gpt54_output_cost_per_million
            + (web_search_calls * self.web_search_cost_per_call)
        )

    def estimate_transcription_cost(self, *, seconds: float) -> float:
        minutes = max(seconds / 60.0, 0.0)
        return minutes * self.transcription_cost_per_minute
