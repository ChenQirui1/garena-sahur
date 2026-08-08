"""A provider that times out, fails, or answers too late never costs a second call.

Owner: Jerome & Richard

Everything here runs through the HTTP intake boundary, so what is asserted is what a caller can
see: which commands were published, which model-call facts were emitted, and how many times the
provider was actually entered. The provider is a fake, so a green run proves the owned pipeline
and not live provider integration.
"""

from __future__ import annotations

from pathlib import Path
from typing import AsyncIterator

import pytest
import pytest_asyncio

from backend.config import Settings
from backend.ingestion.tests.canonical_messages import (
    SESSION_ID,
    SHOPKEEPER,
    active_conversation,
)
from backend.models.mock_provider import MODEL_FOR_TIER, PROVIDER
from backend.models.model_gateway import (
    GeneratedBehaviour,
    GenerationRequest,
    ProviderIdentity,
)
from backend.orchestration.conversation_manager import ConversationState
from backend.orchestration.observations import (
    FALLBACK_USED,
    MODEL_CALL_FAILED,
    MODEL_CALL_TIMED_OUT,
)
from backend.orchestration.router_port import AttentionTier
from backend.orchestration.telemetry_port import STATUS_ERROR
from backend.orchestration.tests.fake_routers import EventAwareRouter
from backend.orchestration.tests.harness import Harness, running, settings_for

# Deliberately different from each other, and deliberately not the shipped defaults. These two
# cases prove that a request reaches *its own* tier's budget, and they can only prove it while the
# two numbers differ — once issue #66 raised Reactive to match Focused at 4,000 ms, asserting the
# shipped values would have let a gateway that always read one tier pass both cases.
#
# The shipped numbers are asserted in `backend/tests/test_config.py`, against `Settings`. Selection
# and value are separate claims and are now separately tested.
FOCUSED_TIMEOUT_MS = 4_500
REACTIVE_TIMEOUT_MS = 1_500


def distinct_tier_budgets(tmp_path: Path) -> Settings:
    """Settings whose two tier budgets cannot be mistaken for each other."""
    return settings_for(
        tmp_path,
        focused_timeout_ms=FOCUSED_TIMEOUT_MS,
        reactive_timeout_ms=REACTIVE_TIMEOUT_MS,
    )


class BrokenProvider:
    """Refuses every call, the way an unreachable provider would."""

    def identity(self, tier: AttentionTier) -> ProviderIdentity:
        return ProviderIdentity(provider="broken", model="broken-model")

    async def generate(self, request: GenerationRequest) -> GeneratedBehaviour:
        raise RuntimeError("provider is unreachable")


@pytest_asyncio.fixture
async def harness(tmp_path: Path) -> AsyncIterator[Harness]:
    async for started in running(settings_for(tmp_path)):
        yield started


async def test_a_focused_call_is_bounded_by_the_focused_budget(tmp_path: Path) -> None:
    """The budget is asserted by value: a Reactive number here would be a real defect."""
    async for held in running(distinct_tier_budgets(tmp_path), gated=True):
        await held.snapshot(active_conversation=active_conversation())
        await held.turn()
        await held.provider.wait_for_started(1)

        expired = await held.deadlines.expire_open()
        await held.settle()

        assert expired == (FOCUSED_TIMEOUT_MS,)
        assert len(held.provider.started) == 1
        assert held.provider.started[0].tier is AttentionTier.FOCUSED


async def test_a_reactive_call_is_bounded_by_its_own_reactive_budget(
    tmp_path: Path,
) -> None:
    settings = distinct_tier_budgets(tmp_path)
    async for held in running(settings, EventAwareRouter(), gated=True):
        # No active conversation, so nothing is Focused and every reaction is Reactive.
        await held.snapshot()
        await held.event()
        await held.provider.wait_for_started(1)

        expired = await held.deadlines.expire_open()
        await held.settle()

        assert expired and set(expired) == {REACTIVE_TIMEOUT_MS}
        assert {one.tier for one in held.provider.started} == {AttentionTier.REACTIVE}


async def test_a_timed_out_call_publishes_fallback_content_and_is_never_repeated(
    tmp_path: Path,
) -> None:
    async for held in running(settings_for(tmp_path), gated=True):
        await held.snapshot(active_conversation=active_conversation())
        await held.turn()
        await held.provider.wait_for_started(1)

        await held.deadlines.expire_open()
        await held.settle()

        assert len(held.provider.started) == 1, "a timeout must not buy a second call"
        assert len(held.publisher.published) == 1

        command = held.publisher.published[0]
        assert command.fallback_used is True
        assert command.dialogue == "Cached for Mira's turn."
        assert command.action is None
        assert held.observed(MODEL_CALL_TIMED_OUT)
        assert held.observed(FALLBACK_USED)[0]["source"] == "npc_and_trigger"


async def test_a_timed_out_attempt_is_reported_as_one_failed_model_call(
    tmp_path: Path,
) -> None:
    async for held in running(settings_for(tmp_path), gated=True):
        await held.snapshot(active_conversation=active_conversation())
        await held.turn()
        await held.provider.wait_for_started(1)

        await held.deadlines.expire_open()
        await held.settle()

        assert len(held.telemetry.model_calls) == 1
        fact = held.telemetry.model_calls[0]
        assert fact.status == STATUS_ERROR
        # `docs/message_schemas.md` §7 allows a null provider only when a request fails before
        # selection, which a timeout is not, so the attempt names what it was waiting on.
        # `fallback_used` is true because this failure always delivers fallback content.
        assert fact.provider == PROVIDER
        assert fact.model == MODEL_FOR_TIER[AttentionTier.FOCUSED]
        assert fact.error_code == "MODEL_TIMEOUT"
        assert fact.fallback_used is True
        assert fact.output_tokens == 0


async def test_a_failing_provider_falls_back_without_retrying(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    async for failing in running(settings, provider=BrokenProvider()):
        await failing.snapshot(active_conversation=active_conversation())
        await failing.turn()
        await failing.settle()

        assert len(failing.provider.started) == 1, "orchestration must not retry a failed call"
        assert len(failing.publisher.published) == 1
        assert failing.publisher.published[0].fallback_used is True
        assert failing.observed(MODEL_CALL_FAILED)
        assert failing.telemetry.model_calls[0].error_code == "PROVIDER_ERROR"
        assert failing.telemetry.model_calls[0].provider == "broken"


async def test_a_fallback_command_still_marks_the_conversation_ready(
    tmp_path: Path,
) -> None:
    async for held in running(settings_for(tmp_path), gated=True):
        await held.snapshot(active_conversation=active_conversation())
        await held.turn()
        await held.provider.wait_for_started(1)

        await held.deadlines.expire_open()
        await held.settle()

        assert held.state() is ConversationState.READY


async def test_work_superseded_before_the_timeout_produces_no_fallback_command(
    tmp_path: Path,
) -> None:
    """A newer turn replaces the pending one; the abandoned attempt must publish nothing."""
    async for held in running(settings_for(tmp_path), gated=True):
        await held.snapshot(active_conversation=active_conversation())
        await held.turn()
        await held.provider.wait_for_started(1)

        # The conversation ends, so the target drops out of every generating tier and the work
        # in flight is no longer current when the provider finally gives up.
        await held.snapshot(sequence=1843)
        await held.settle_routing()
        await held.deadlines.expire_open()
        await held.settle()

        assert held.publisher.published == [], "stale work must not publish a fallback"
        assert await held.pipeline.commands.unpublished() == (), "nothing may be stored"
        assert await held.pipeline.commands.latest_for(SESSION_ID, SHOPKEEPER) is None


async def test_a_cancelled_event_reaction_produces_no_fallback_command(
    tmp_path: Path,
) -> None:
    """The event ends while its reaction is in flight, so the spent attempt is discarded."""
    settings = settings_for(tmp_path)
    async for held in running(settings, EventAwareRouter(), gated=True):
        await held.snapshot()
        await held.event(revision=1)
        await held.provider.wait_for_started(1)

        await held.event(revision=2, status="ended")
        await held.deadlines.expire_open()
        await held.settle()

        assert held.publisher.published == []
        assert await held.pipeline.commands.unpublished() == (), "nothing may be stored"


async def test_a_second_delivery_after_a_fallback_does_not_call_the_provider_again(
    tmp_path: Path,
) -> None:
    async for held in running(settings_for(tmp_path), gated=True):
        await held.snapshot(active_conversation=active_conversation())
        await held.turn()
        await held.provider.wait_for_started(1)
        await held.deadlines.expire_open()
        await held.settle()

        redelivered = await held.turn()
        await held.settle()

        assert redelivered.status_code == 200
        assert len(held.provider.started) == 1
        assert len(held.publisher.published) == 1
