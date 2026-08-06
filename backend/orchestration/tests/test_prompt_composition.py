"""What the running pipeline actually hands a provider.

Owner: Jerome & Richard

The renderer cases beneath `backend/models/prompts/` prove each renderer in isolation. These
prove the wiring: that the context path a generation took decides the prompt it gets, including
for promotion and expiry, which reuse whichever path still requires foreground behaviour and so
cannot be classified by their own trigger name.

The provider fake records every request it is handed, so the prompt — a string no command, fact,
or stored record carries — is observable here and nowhere else.
"""

from __future__ import annotations

from pathlib import Path

from backend.context.context_builder import TriggerKind
from backend.ingestion.tests.canonical_messages import (
    SHOPKEEPER,
    THIEF,
    active_conversation,
)
from backend.models.model_gateway import GenerationRequest
from backend.models.prompts.focused_prompt import PLAYER_HEADING, SITUATION_HEADING
from backend.orchestration.router_port import AttentionTier
from backend.orchestration.tests.fake_routers import TierScriptRouter
from backend.orchestration.tests.harness import Harness, running, settings_for

AMBIENT = AttentionTier.AMBIENT
FOCUSED = AttentionTier.FOCUSED

TURN_TEXT = "Which direction did the thief run?"


def request_to(harness: Harness, npc_id: str) -> GenerationRequest:
    started = [one for one in harness.provider.started if one.npc_id == npc_id]
    assert len(started) == 1, f"expected exactly one call for {npc_id}, saw {len(started)}"
    return started[0]


async def test_a_turn_reaches_the_provider_as_something_the_player_said(
    tmp_path: Path,
) -> None:
    router = TierScriptRouter({SHOPKEEPER: FOCUSED})
    async for harness in running(settings_for(tmp_path), router=router):
        await harness.snapshot(active_conversation=active_conversation())
        await harness.turn()
        await harness.settle()

        request = request_to(harness, SHOPKEEPER)
        assert request.trigger_kind is TriggerKind.PLAYER_SPEECH
        assert f"{PLAYER_HEADING}\nThe player says: \"{TURN_TEXT}\"" in request.prompt


async def test_an_event_reaction_reaches_the_provider_as_something_happening(
    tmp_path: Path,
) -> None:
    """The defect: the strongest model was told the player had said the event out loud."""
    router = TierScriptRouter({THIEF: FOCUSED})
    async for harness in running(settings_for(tmp_path), router=router):
        await harness.snapshot()
        await harness.event()
        await harness.settle()

        request = request_to(harness, THIEF)
        assert request.trigger_kind is TriggerKind.OBSERVED_EVENT
        assert PLAYER_HEADING not in request.prompt
        assert f"{SITUATION_HEADING}\n{request.trigger_text}" in request.prompt
        assert "market theft" in request.trigger_text


async def test_a_promotion_onto_a_past_event_renders_as_a_reaction_not_as_speech(
    tmp_path: Path,
) -> None:
    """Promotion is a reason to speak, not a thing spoken; here it reuses the event path.

    Classifying by the `Trigger` member would leave this rendering under whichever heading
    promotion was arbitrarily assigned, which is exactly the mistake the trigger kind exists to
    make impossible.
    """
    router = TierScriptRouter({SHOPKEEPER: AMBIENT})
    async for harness in running(settings_for(tmp_path), router=router):
        await harness.snapshot(sequence=1)
        await harness.settle()
        await harness.event(actor_npc_ids=[THIEF], target_npc_ids=[SHOPKEEPER])
        await harness.settle()
        assert harness.provider.started == []

        router.tiers[SHOPKEEPER] = FOCUSED
        await harness.snapshot(sequence=2)
        await harness.settle()

        request = request_to(harness, SHOPKEEPER)
        assert request.trigger == "promotion"
        assert request.trigger_kind is TriggerKind.OBSERVED_EVENT
        assert PLAYER_HEADING not in request.prompt
        assert SITUATION_HEADING in request.prompt


async def test_the_mock_reply_to_an_event_never_claims_the_player_asked_anything(
    tmp_path: Path,
) -> None:
    router = TierScriptRouter({THIEF: FOCUSED})
    async for harness in running(settings_for(tmp_path), router=router):
        await harness.snapshot()
        await harness.event()
        await harness.settle()

        spoken = [command.dialogue for command in harness.published_for(THIEF)]
        assert spoken and all("You asked" not in (line or "") for line in spoken)


async def test_the_mock_reply_to_a_turn_still_repeats_the_question_back(
    tmp_path: Path,
) -> None:
    """The conversation wording is unchanged, so the fix is visibly confined to the other path."""
    router = TierScriptRouter({SHOPKEEPER: FOCUSED})
    async for harness in running(settings_for(tmp_path), router=router):
        await harness.snapshot(active_conversation=active_conversation())
        await harness.turn()
        await harness.settle()

        spoken = [command.dialogue for command in harness.published_for(SHOPKEEPER)]
        assert spoken == [f'Mira leans in. "You asked: {TURN_TEXT} Let me tell you what I saw."']
