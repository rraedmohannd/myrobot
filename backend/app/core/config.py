"""Application settings, loaded from environment variables / .env (never hard-coded)."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Smart Campus Robot Backend"
    app_env: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"

    # postgresql+asyncpg://user:password@host:5432/dbname
    database_url: str = Field(
        default="postgresql+asyncpg://robot:change-me@localhost:5433/smart_campus"
    )

    # Origins allowed to call the REST API (admin panel). Comma-separated in .env.
    cors_origins: str = "http://localhost:5173"

    # AI providers (selected through adapters; not used before phase B3/B4).
    llm_provider: Literal["openai", "anthropic"] = "openai"
    stt_provider: Literal["openai"] = "openai"
    tts_provider: Literal["openai"] = "openai"
    openai_api_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None

    # Token for the admin API/page (sent as X-Admin-Token). Empty = admin API disabled.
    admin_token: SecretStr | None = None

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
