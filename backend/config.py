"""Backend environment variables read from the process environment and `.env`.

Owner: Jerome & Richard
"""

from __future__ import annotations

from pathlib import Path
from typing import Final, Literal

from openai.types.shared.reasoning_effort import ReasoningEffort
from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Which adapter answers a generation request. Mock mode is the default because specification #1
# requires development and rehearsal not to depend on external model availability, so a live call
# is something a deployment opts into rather than something it opts out of.
ProviderMode = Literal["mock", "openai"]
PROVIDER_MODE_MOCK: Final[ProviderMode] = "mock"
PROVIDER_MODE_OPENAI: Final[ProviderMode] = "openai"


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

    # Specification #1 proposed 15 seconds from creation, which is also the maximum Minecraft
    # accepts. It cannot be both. `GameEventPublisher.isCurrentEvent` keeps an event
    # command-current for the same 15,000 ms but measures from event *publish*, while a command is
    # created at generation — after intake, routing, queue wait and the provider call. At 15,000 ms
    # the two windows are the same length and offset, so the tail of every event-triggered
    # command's retry window falls outside the mod's currency window and is rejected as a stale
    # trigger, which is exactly the restart-recovery case that window exists to serve.
    #
    # 10 seconds leaves 5 seconds of headroom: the 4-second provider timeout plus a second for
    # transport, intake, routing and context. Queue wait is not bounded, so this makes the window
    # fit in the ordinary case rather than provably in every case.
    #
    # The 4 seconds is the *larger* of the two tier timeouts, not the Focused one specifically.
    # Issue #66 raised Reactive to the same 4,000 ms, so Reactive-triggered commands now sit at
    # the same worst-case offset Focused ones always have; the headroom itself did not move.
    # Whichever tier timeout is raised above 4,000 ms is the one that forces this value down. Jerome decided on
    # 2026-08-08 to shorten ours rather than ask Ivan to widen his (issue #59); the cost is that
    # fewer commands survive a restart, recorded in `orchestration/recovery.py`.
    command_lifetime_ms: int = Field(default=10_000, gt=0)

    # Specification #1: Focused calls time out after four seconds and Reactive after two. The
    # provider never sees a retry, here or in its own SDK, so this budget is the whole allowance
    # for one attempt rather than the allowance for a first try.
    #
    # Reactive departs from that two seconds, by Jerome's decision on 2026-08-08 (issue #66). The
    # first live measurements showed the two models have the same latency profile: over 20 calls
    # each, `gpt-5.6-luna` ran a 1,164 ms median with a 3,830 ms tail against `gpt-5.6-terra`'s
    # 1,458 ms and 3,468 ms. Reactive is the cheaper model, not a faster one, and its p90 of
    # 2,261 ms already sat outside a 2,000 ms budget — so roughly one background NPC line in ten
    # was cached fallback rather than generated dialogue.
    #
    # Four seconds costs nothing downstream: `command_lifetime_ms` is derived from the *larger*
    # tier timeout, and Focused already fixes that at 4,000 ms. Raising Reactive to meet it leaves
    # the worst case exactly where it was. What it does cost is slot occupancy — see
    # `reactive_concurrency` below.
    focused_timeout_ms: int = Field(default=4_000, gt=0)
    reactive_timeout_ms: int = Field(default=4_000, gt=0)

    # Specification #1: publication retries occur after approximately 100 ms, 250 ms, 500 ms and
    # 1 second, and may repeat that cadence while the same command is still within its lifetime.
    publication_retry_delays_ms: tuple[int, ...] = (100, 250, 500, 1_000)

    # Specification #1: provider concurrency is capped at two Focused and six Reactive calls,
    # with eight in total. These bound outbound model calls and are a different quantity from
    # the Router's Focused and Reactive *capacities*, which happen to carry the same numbers
    # and are owned by Elson & Daniel behind the Router port.
    #
    # These caps and the tier timeouts multiply: six Reactive slots held for up to 4,000 ms is a
    # worst-case 1.5 reactions per second, where the old 2,000 ms budget allowed 3. Issue #66
    # accepted that on the grounds that a slow generated line beats a prompt cached one, and left
    # the measurement to #13's rehearsal, which is the first run with a real crowd behind it.
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

    # Specification #1: "Focused uses the configured OpenAI `gpt-5.6-terra` adapter... Reactive
    # uses the configured OpenAI `gpt-5.6-luna` adapter... These are configuration defaults, not
    # orchestration dependencies." They are settings rather than constants for that reason: a
    # different vendor or model is a deployment change, and nothing outside the two provider
    # adapters reads them.
    provider_mode: ProviderMode = PROVIDER_MODE_MOCK
    focused_model: str = "gpt-5.6-terra"
    reactive_model: str = "gpt-5.6-luna"

    # Specification #1 disables reasoning on both tiers, so `none` is what ships and what every
    # demo runs. It is a setting rather than a constant because `docs/team-architecture.md` makes
    # this file the home of "model configuration", and because a tier whose latency turns out
    # wrong at rehearsal should be answerable without a code change.
    #
    # The type is the SDK's own, not a copy of its list, so a vocabulary change arrives with the
    # pinned dependency instead of drifting. `None` omits the parameter and takes whatever the
    # model does by default — which is *not* the same as disabled, and is why the default here is
    # the explicit `none` rather than nothing. Specification #1 still rules out sweeping this
    # value as an experiment; the knob exists to be set once per deployment.
    reasoning_effort: ReasoningEffort = "none"

    # Held as a secret so that a settings repr — an error page, a log line, a debugger — cannot
    # spill it. Absent by default: mock mode never reads it, and live mode without it makes the
    # service unready rather than unstartable (`backend/main.py`).
    openai_api_key: SecretStr | None = None

    # Specification #1: these five ceilings are its numbers. Reactive sees only the triggering
    # turn, which is why no Reactive history setting sits beside the Focused one.
    focused_input_token_limit: int = Field(default=2_000, gt=0)
    focused_output_token_limit: int = Field(default=120, gt=0)
    focused_history_turns: int = Field(default=8, ge=0)
    reactive_input_token_limit: int = Field(default=600, gt=0)
    reactive_output_token_limit: int = Field(default=40, gt=0)

    # The development compatibility bridge for the shipped Fabric mod's prototype wire
    # (`backend/ingestion/prototype_bridge.py`, issue #57). Off here because it is a departure
    # from the deployment boundary that ADR 0012 records and issue #11 retires; a demo run turns
    # it on. The three values below are what §1 requires of every snapshot and the mod sends in
    # none of them: the radii are Ivan's confirmed `SpotlightConfig` constants (#2 A9), and the
    # world is a single-world stand-in until the mod sends its dimension key (#2 A12).
    prototype_bridge_enabled: bool = False
    prototype_world_id: str = "minecraft-overworld"
    prototype_entry_radius_blocks: float = Field(default=24.0, ge=0)
    prototype_exit_radius_blocks: float = Field(default=28.0, ge=0)

    @model_validator(mode="after")
    def check_prototype_radii_are_orderable(self) -> Settings:
        """§1 requires the exit radius to exceed the entry radius.

        Enforced here rather than left to intake: transposed radii would otherwise start a
        service that rejects every snapshot it is sent, which reads as a broken mod.
        """
        if self.prototype_exit_radius_blocks <= self.prototype_entry_radius_blocks:
            raise ValueError(
                "prototype_exit_radius_blocks must be greater than"
                " prototype_entry_radius_blocks"
            )
        return self


def load_settings() -> Settings:
    return Settings()
