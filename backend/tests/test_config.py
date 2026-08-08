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


def test_transposed_prototype_radii_are_refused_at_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`docs/message_schemas.md` §1 requires the exit radius to exceed the entry radius.

    Left to intake, the two values would start a service that rejects every snapshot the mod
    sends it — a failure that reads as a broken publisher rather than as a broken `.env`.
    """
    monkeypatch.setenv(f"{ENV_PREFIX}PROTOTYPE_ENTRY_RADIUS_BLOCKS", "28.0")
    monkeypatch.setenv(f"{ENV_PREFIX}PROTOTYPE_EXIT_RADIUS_BLOCKS", "24.0")

    with pytest.raises(ValueError, match="prototype_exit_radius_blocks"):
        settings_from_environment()


def test_ordered_prototype_radii_are_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    """The case above has to be refusing the order, not the pair of variables."""
    monkeypatch.setenv(f"{ENV_PREFIX}PROTOTYPE_ENTRY_RADIUS_BLOCKS", "10.0")
    monkeypatch.setenv(f"{ENV_PREFIX}PROTOTYPE_EXIT_RADIUS_BLOCKS", "12.0")

    assert settings_from_environment().prototype_exit_radius_blocks == 12.0


def test_loading_settings_reads_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """`load_settings` is what `main` calls, so the environment has to reach it too."""
    monkeypatch.setenv(f"{ENV_PREFIX}LOG_LEVEL", "debug")

    assert load_settings().log_level == "debug"


def test_the_command_lifetime_is_read_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A demo host may need a different value, so it has to be a knob rather than a constant."""
    monkeypatch.setenv(f"{ENV_PREFIX}COMMAND_LIFETIME_MS", "8000")

    assert settings_from_environment().command_lifetime_ms == 8_000


def test_the_provider_mode_is_read_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Live calls are opted into by a deployment, so the switch has to survive parsing."""
    monkeypatch.setenv(f"{ENV_PREFIX}PROVIDER_MODE", "openai")

    assert settings_from_environment().provider_mode == "openai"


def test_an_unknown_provider_mode_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """A typo must not silently resolve to whichever mode the default happens to be."""
    monkeypatch.setenv(f"{ENV_PREFIX}PROVIDER_MODE", "anthropic")

    with pytest.raises(ValueError):
        settings_from_environment()


def test_the_model_identifiers_are_read_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Specification #1 calls these configuration defaults, which they only are if a
    deployment can actually change them without a code change."""
    monkeypatch.setenv(f"{ENV_PREFIX}FOCUSED_MODEL", "another-strong-model")
    monkeypatch.setenv(f"{ENV_PREFIX}REACTIVE_MODEL", "another-cheap-model")

    settings = settings_from_environment()

    assert settings.focused_model == "another-strong-model"
    assert settings.reactive_model == "another-cheap-model"


def test_the_shipped_model_defaults_are_the_specified_ones() -> None:
    assert Settings.model_fields["focused_model"].default == "gpt-5.6-terra"
    assert Settings.model_fields["reactive_model"].default == "gpt-5.6-luna"


@pytest.mark.parametrize(
    ("field", "specified"),
    [
        ("focused_input_token_limit", 2_000),
        ("focused_output_token_limit", 120),
        ("focused_history_turns", 8),
        ("focused_timeout_ms", 4_000),
        ("reactive_input_token_limit", 600),
        ("reactive_output_token_limit", 40),
        ("reactive_timeout_ms", 2_000),
    ],
)
def test_a_shipped_tier_budget_is_the_number_specification_1_fixed(
    field: str, specified: int
) -> None:
    """Specification #1 fixes each of these, and issue #12 restates them as criteria.

    They were enforced but unasserted: cases elsewhere pass a budget in or restate it as a
    literal, so editing a default here moved what the service ships without moving a test. That
    is exactly how the 15,000 ms command lifetime issue #59 corrected survived unnoticed.
    """
    assert Settings.model_fields[field].default == specified


def test_the_api_key_is_read_from_the_environment_and_not_repeated_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Held as a secret so an error page, a log line, or a debugger cannot spill it —
    `docs/message_schemas.md` §8 says never to place provider credentials in payloads, and a
    settings repr reaches further than a payload does."""
    monkeypatch.setenv(f"{ENV_PREFIX}OPENAI_API_KEY", "sk-not-a-real-key")

    settings = settings_from_environment()

    assert settings.openai_api_key is not None
    assert settings.openai_api_key.get_secret_value() == "sk-not-a-real-key"
    assert "sk-not-a-real-key" not in repr(settings)


def test_the_api_key_is_absent_by_default() -> None:
    """Mock mode never reads it, so an unconfigured checkout runs the whole owned pipeline."""
    assert Settings.model_fields["openai_api_key"].default is None


def test_the_default_command_lifetime_closes_inside_minecrafts_currency_window() -> None:
    """Ivan's `GameEventPublisher.isCurrentEvent` keeps an event command-current for 15,000 ms
    measured from event *publish*; our command is created later, at generation. Equal lengths
    would put the tail of every event-triggered command's retry window outside that window, where
    it is refused as a stale trigger (issue #59). The headroom is for the 4,000 ms Focused
    provider timeout plus intake, routing and context.
    """
    MINECRAFT_EVENT_CURRENCY_WINDOW_MS = 15_000
    FOCUSED_TIMEOUT_MS = Settings.model_fields["focused_timeout_ms"].default

    lifetime = Settings.model_fields["command_lifetime_ms"].default

    assert lifetime < MINECRAFT_EVENT_CURRENCY_WINDOW_MS
    assert MINECRAFT_EVENT_CURRENCY_WINDOW_MS - lifetime > FOCUSED_TIMEOUT_MS
