"""Which adapter configuration selects, and what a missing secret does.

Owner: Jerome & Richard

The seam is `build_pipeline`: configuration decides who answers a generation request, and nothing
downstream of the gateway knows which mode it got. These cases assert that from the outside — the
provider a running pipeline would call, and whether the service reports itself ready — rather than
by reading the wiring.

Live mode is exercised without a network. The point being made is about selection and readiness,
and a case that needed a key would be a case that only ran on a machine that had one.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from backend.config import PROVIDER_MODE_MOCK, PROVIDER_MODE_OPENAI, Settings
from backend.main import Adapters, PipelineNotReady, build_pipeline, create_app
from backend.models.mock_provider import MODEL_FOR_TIER
from backend.models.mock_provider import PROVIDER as MOCK_PROVIDER
from backend.models.openai_provider import PROVIDER as OPENAI_PROVIDER
from backend.orchestration.router_port import AttentionTier
from backend.orchestration.tests.fake_routers import RecordingRouter

KEY = "sk-not-a-real-key"


def settings_for(tmp_path: Path, **overrides: object) -> Settings:
    return Settings(
        database_path=tmp_path / "spotlight.sqlite3",
        npc_profiles_path=Path("data/npc_profiles.json"),
        cached_dialogue_path=Path("data/cached_dialogue.json"),
        **overrides,  # type: ignore[arg-type]
    )


def test_mock_mode_reaches_the_deterministic_provider(tmp_path: Path) -> None:
    pipeline = build_pipeline(settings_for(tmp_path, provider_mode=PROVIDER_MODE_MOCK))

    identity = pipeline.generation.gateway.identity_for(AttentionTier.FOCUSED)

    assert identity is not None
    assert identity.provider == MOCK_PROVIDER
    assert identity.model == MODEL_FOR_TIER[AttentionTier.FOCUSED]
    assert pipeline.readiness_error is None


def test_mock_mode_needs_no_secret_at_all(tmp_path: Path) -> None:
    """Specification #1: development and rehearsal must not depend on external availability."""
    pipeline = build_pipeline(
        settings_for(tmp_path, provider_mode=PROVIDER_MODE_MOCK, openai_api_key=None)
    )

    assert pipeline.readiness_error is None


def test_mock_mode_is_what_an_unconfigured_deployment_gets() -> None:
    """A live call is opted into. A default that called out would bill the first demo run."""
    assert Settings.model_fields["provider_mode"].default == PROVIDER_MODE_MOCK


@pytest.mark.parametrize(
    ("tier", "expected_model"),
    [(AttentionTier.FOCUSED, "gpt-5.6-terra"), (AttentionTier.REACTIVE, "gpt-5.6-luna")],
)
def test_live_mode_reaches_each_tiers_configured_openai_model(
    tmp_path: Path, tier: AttentionTier, expected_model: str
) -> None:
    pipeline = build_pipeline(
        settings_for(tmp_path, provider_mode=PROVIDER_MODE_OPENAI, openai_api_key=KEY)
    )

    identity = pipeline.generation.gateway.identity_for(tier)

    assert identity is not None
    assert identity.provider == OPENAI_PROVIDER
    assert identity.model == expected_model
    assert pipeline.readiness_error is None


def test_the_configured_models_are_what_live_mode_calls(tmp_path: Path) -> None:
    """The identifiers are configuration, so a deployment changes them without a code change."""
    pipeline = build_pipeline(
        settings_for(
            tmp_path,
            provider_mode=PROVIDER_MODE_OPENAI,
            openai_api_key=KEY,
            focused_model="another-strong-model",
            reactive_model="another-cheap-model",
        )
    )

    focused = pipeline.generation.gateway.identity_for(AttentionTier.FOCUSED)
    reactive = pipeline.generation.gateway.identity_for(AttentionTier.REACTIVE)

    assert focused is not None and focused.model == "another-strong-model"
    assert reactive is not None and reactive.model == "another-cheap-model"


@pytest.mark.parametrize("key", [None, "", "   "])
def test_live_mode_without_a_usable_secret_is_unready(tmp_path: Path, key: str | None) -> None:
    """Blank counts as missing: `SPOTLIGHT_OPENAI_API_KEY=` is what an uncommented but unfilled
    `.env` line leaves behind, and it must not buy a 401 in the middle of the demo."""
    pipeline = build_pipeline(
        settings_for(tmp_path, provider_mode=PROVIDER_MODE_OPENAI, openai_api_key=key)
    )

    assert pipeline.readiness_error is not None
    assert "SPOTLIGHT_OPENAI_API_KEY" in pipeline.readiness_error


async def test_an_unready_live_deployment_refuses_to_drain_rather_than_calling_out(
    tmp_path: Path,
) -> None:
    """Unready is not merely a reported status: the pipeline refuses the work as well.

    The process still starts and still answers liveness, which is what makes a missing secret a
    configuration problem an operator can see rather than a crash loop.
    """
    pipeline = build_pipeline(
        settings_for(tmp_path, provider_mode=PROVIDER_MODE_OPENAI, openai_api_key=None),
        Adapters(router=RecordingRouter()),
    )

    async with pipeline.running():
        assert pipeline.is_ready is False
        with pytest.raises(PipelineNotReady, match="SPOTLIGHT_OPENAI_API_KEY"):
            await pipeline.drain()


async def test_readiness_reports_the_missing_secret_over_http(tmp_path: Path) -> None:
    app = create_app(
        settings=settings_for(
            tmp_path, provider_mode=PROVIDER_MODE_OPENAI, openai_api_key=None
        ),
        adapters=Adapters(router=RecordingRouter()),
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://backend") as client:
        async with LifespanManager(app):
            assert (await client.get("/health/live")).json() == {"status": "alive"}
            assert (await client.get("/health/ready")).status_code == 503


def test_a_named_adapter_still_wins_over_configuration(tmp_path: Path) -> None:
    """Every owned suite substitutes a provider, and live configuration must not override it."""
    from backend.models.mock_provider import MockProvider

    substituted = MockProvider(characters_per_token=7)
    pipeline = build_pipeline(
        settings_for(tmp_path, provider_mode=PROVIDER_MODE_OPENAI, openai_api_key=KEY),
        Adapters(provider=substituted),
    )

    identity = pipeline.generation.gateway.identity_for(AttentionTier.FOCUSED)

    assert identity is not None
    assert identity.provider == MOCK_PROVIDER
