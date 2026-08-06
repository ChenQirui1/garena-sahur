"""Owner: Jerome & Richard

`Settings` is the layer between the deployed environment and every bound the backend enforces,
and it is the one layer a test that constructs `Settings()` directly never exercises: defaults
pass whether or not the environment is read at all. These cases read the environment instead, so
a broken prefix, a type that no longer parses, or a dropped key fails here rather than in a
deployment where the value silently reverts to its default.

Every case passes `_env_file=None`. A developer's local `.env` is not part of the contract, and a
suite that reads one passes or fails on a file no reviewer can see.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.config import Settings, default_database_path, load_settings

ENV_PREFIX = "SPOTLIGHT_"


def settings_from_environment() -> Settings:
    return Settings(_env_file=None)  # type: ignore[call-arg]


def test_a_numeric_setting_is_read_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(f"{ENV_PREFIX}FOCUSED_TIMEOUT_MS", "7500")

    assert settings_from_environment().focused_timeout_ms == 7_500


def test_a_path_setting_is_read_from_the_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The database path defaults outside the checkout, so an override has to be visible."""
    override = tmp_path / "spotlight.sqlite3"
    monkeypatch.setenv(f"{ENV_PREFIX}DATABASE_PATH", str(override))

    settings = settings_from_environment()

    assert settings.database_path == override
    assert settings.database_path != default_database_path()


def test_a_list_setting_is_read_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The retry cadence is a sequence, so its order and length both have to survive parsing."""
    monkeypatch.setenv(f"{ENV_PREFIX}PUBLICATION_RETRY_DELAYS_MS", "[10, 20, 30]")

    assert settings_from_environment().publication_retry_delays_ms == (10, 20, 30)


def test_an_unprefixed_variable_is_not_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """`PORT` belongs to whatever else runs on the host; only `SPOTLIGHT_PORT` is ours."""
    monkeypatch.setenv("PORT", "9999")

    assert settings_from_environment().port == Settings.model_fields["port"].default


def test_an_out_of_range_environment_value_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bound that the environment can walk past is not a bound."""
    monkeypatch.setenv(f"{ENV_PREFIX}PORT", "70000")

    with pytest.raises(ValueError):
        settings_from_environment()


def test_loading_settings_reads_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """`load_settings` is what `main` calls, so the environment has to reach it too."""
    monkeypatch.setenv(f"{ENV_PREFIX}LOG_LEVEL", "debug")

    assert load_settings().log_level == "debug"
