"""Backend environment variables read from the process environment and `.env`.

Owner: Jerome & Richard
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def default_database_path() -> Path:
    """Keep operational state on the device rather than in the checkout."""
    return Path.home() / ".spotlight" / "spotlight.sqlite3"


class Settings(BaseSettings):
    """Environment-based service configuration, prefixed ``SPOTLIGHT_``."""

    model_config = SettingsConfigDict(
        env_prefix="SPOTLIGHT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    # Deployment knobs, not contract-derived: no shared document constrains where the process
    # binds or where it keeps its files, so these carry no citation rather than a borrowed one.
    # Every field below this block traces to a source, and none of them bounds upstream data.
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    log_level: str = "info"

    database_path: Path = Field(default_factory=default_database_path)
    npc_profiles_path: Path = Path("data/npc_profiles.json")
    cached_dialogue_path: Path = Path("data/cached_dialogue.json")

    # Specification #1: a command is acceptable for 15 seconds from its creation.
    command_lifetime_ms: int = Field(default=15_000, gt=0)

    # Specification #1: Focused calls time out after four seconds and Reactive after two. The
    # provider never sees a retry, here or in its own SDK, so this budget is the whole allowance
    # for one attempt rather than the allowance for a first try.
    focused_timeout_ms: int = Field(default=4_000, gt=0)
    reactive_timeout_ms: int = Field(default=2_000, gt=0)

    # Specification #1: publication retries occur after approximately 100 ms, 250 ms, 500 ms and
    # 1 second, and may repeat that cadence while the same command is still within its lifetime.
    publication_retry_delays_ms: tuple[int, ...] = (100, 250, 500, 1_000)

    # Specification #1: provider concurrency is capped at two Focused and six Reactive calls,
    # with eight in total. These bound outbound model calls and are a different quantity from
    # the Router's Focused and Reactive *capacities*, which happen to carry the same numbers
    # and are owned by Elson & Daniel behind the Router port.
    focused_concurrency: int = Field(default=2, gt=0)
    reactive_concurrency: int = Field(default=6, gt=0)
    total_concurrency: int = Field(default=8, gt=0)

    # Specification #1: witness membership freezes at event start within 12 blocks, and nearby
    # spans 12 to 24 blocks. Both are configurable because it asks for that, and a different
    # scene may need a different scale.
    witness_radius_blocks: float = Field(default=12.0, ge=0)
    nearby_radius_blocks: float = Field(default=24.0, ge=0)

    # No tokenizer dependency is scoped by any issue, so the token ceilings below are enforced
    # against a deterministic character estimate rather than a real encoder.
    characters_per_token: int = Field(default=4, gt=0)

    # Specification #1: these five ceilings are its numbers. Reactive sees only the triggering
    # turn, which is why no Reactive history setting sits beside the Focused one.
    focused_input_token_limit: int = Field(default=2_000, gt=0)
    focused_output_token_limit: int = Field(default=120, gt=0)
    focused_history_turns: int = Field(default=8, ge=0)
    reactive_input_token_limit: int = Field(default=600, gt=0)
    reactive_output_token_limit: int = Field(default=40, gt=0)


def load_settings() -> Settings:
    return Settings()
