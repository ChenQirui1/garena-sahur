"""Backend environment variables read from the process environment and `.env`.

Owner: Jerome & Richard
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-based service configuration, prefixed ``SPOTLIGHT_``."""

    model_config = SettingsConfigDict(
        env_prefix="SPOTLIGHT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    log_level: str = "info"
    max_snapshot_candidates: int = Field(default=128, ge=1, le=4096)


def load_settings() -> Settings:
    return Settings()
