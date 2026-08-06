"""Owner: Jerome & Richard"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator

import pytest
import pytest_asyncio

from backend.context.context_builder import (
    EVENT,
    HISTORY,
    OUTPUT_CONTRACT,
    PROFILE,
    TRIGGER,
    WORLD,
    ContextBuilder,
    ContextLimits,
    GenerationContext,
)
from backend.context.conversation_history import ConversationHistory
from backend.context.event_context import ActiveEvents, describe_event
from backend.context.npc_profiles import NpcProfiles
from backend.ingestion.durable_store import DurableStore
from backend.ingestion.event_store import EventStore
from backend.ingestion.message_validation import (
    GameEvent,
    validate_conversation_turn,
    validate_game_event,
    validate_world_snapshot,
)
from backend.ingestion.tests.canonical_messages import (
    EVENT_TIMESTAMP_MS,
    GUARD,
    SHOPKEEPER,
    THIEF,
    conversation_turn,
    game_event,
    npc,
    world_snapshot,
)
from backend.ingestion.turn_store import TurnStore
from backend.models.token_estimate import estimate_tokens
from backend.orchestration.event_relevance import (
    ROLE_RESPONDER,
    ROLE_TARGET,
    ROLE_WITNESS,
    EventRadii,
)
from backend.orchestration.router_port import AttentionTier

CHARACTERS_PER_TOKEN = 4
FOCUSED = ContextLimits(input_tokens=2_000, output_tokens=120, history_turns=8)
REACTIVE = ContextLimits(input_tokens=600, output_tokens=40, history_turns=0)
RADII = EventRadii(witness_blocks=12.0, nearby_blocks=24.0)

TRIGGERING_TURN = validate_conversation_turn(conversation_turn(turn_index=20))
SNAPSHOT = validate_world_snapshot(world_snapshot())

# The turn targets the shopkeeper, so each event below gives it one role and no other: robbed
# in the theft, merely a bystander to the brawl, and called out to the fire.
THEFT = validate_game_event(game_event())
BRAWL = validate_game_event(
    game_event(
        event_id="market-brawl-002",
        message_id="event-message-002",
        timestamp_ms=EVENT_TIMESTAMP_MS - 100,
        event_type="market_brawl",
        actor_npc_ids=[THIEF],
        target_npc_ids=[GUARD],
        responder_npc_ids=[],
    )
)
FIRE = validate_game_event(
    game_event(
        event_id="market-fire-003",
        message_id="event-message-003",
        timestamp_ms=EVENT_TIMESTAMP_MS + 100,
        event_type="stall_fire",
        actor_npc_ids=[],
        target_npc_ids=[],
        responder_npc_ids=[SHOPKEEPER],
    )
)


@dataclass(frozen=True, slots=True)
class Parts:
    builder: ContextBuilder
    turns: TurnStore
    events: EventStore

    async def activate(self, event: GameEvent, witnesses: frozenset[str]) -> None:
        await self.events.record(event, witnesses)

    def with_input_tokens(self, tokens: int) -> ContextBuilder:
        """The same builder against a budget too small to hold everything."""
        return _builder(
            self.turns,
            self.events,
            focused=ContextLimits(
                input_tokens=tokens,
                output_tokens=FOCUSED.output_tokens,
                history_turns=FOCUSED.history_turns,
            ),
        )


def _builder(
    turns: TurnStore, events: EventStore, focused: ContextLimits = FOCUSED
) -> ContextBuilder:
    return ContextBuilder(
        profiles=NpcProfiles.empty(),
        history=ConversationHistory(turns),
        events=ActiveEvents(events, RADII),
        focused=focused,
        reactive=REACTIVE,
        characters_per_token=CHARACTERS_PER_TOKEN,
    )


async def _with_history(turns: TurnStore, count: int, words: int = 4) -> None:
    for index in range(count):
        await turns.record(
            validate_conversation_turn(
                conversation_turn(
                    turn_id=f"turn-{index}",
                    turn_index=index,
                    text=" ".join(f"word{index}" for _ in range(words)),
                )
            )
        )


def _body(context: GenerationContext, name: str) -> str:
    return next(section.body for section in context.sections if section.name == name)


@pytest_asyncio.fixture
async def parts(tmp_path: Path) -> AsyncIterator[Parts]:
    store = DurableStore(tmp_path / "spotlight.sqlite3")
    await store.open()
    turns = TurnStore(store)
    events = EventStore(store)
    try:
        yield Parts(builder=_builder(turns, events), turns=turns, events=events)
    finally:
        await store.close()


def test_the_token_estimate_is_a_ceiling_not_a_floor() -> None:
    assert estimate_tokens("", 4) == 0
    assert estimate_tokens("abc", 4) == 1
    assert estimate_tokens("abcd", 4) == 1
    assert estimate_tokens("abcde", 4) == 2


async def test_focused_context_is_priority_ordered(
    parts: Parts,
) -> None:
    builder, turns = parts.builder, parts.turns
    await _with_history(turns, 2)

    context = await builder.build(AttentionTier.FOCUSED, TRIGGERING_TURN, SNAPSHOT)

    assert [section.name for section in context.sections] == [
        OUTPUT_CONTRACT,
        TRIGGER,
        PROFILE,
        WORLD,
        HISTORY,
    ]
    assert context.output_token_limit == 120


async def test_the_same_facts_always_produce_the_same_context(
    parts: Parts,
) -> None:
    builder, turns = parts.builder, parts.turns
    await _with_history(turns, 3)

    first = await builder.build(AttentionTier.FOCUSED, TRIGGERING_TURN, SNAPSHOT)
    second = await builder.build(AttentionTier.FOCUSED, TRIGGERING_TURN, SNAPSHOT)

    assert first == second


async def test_focused_history_stops_at_the_permitted_number_of_turns(
    parts: Parts,
) -> None:
    builder, turns = parts.builder, parts.turns
    await _with_history(turns, 15)

    context = await builder.build(AttentionTier.FOCUSED, TRIGGERING_TURN, SNAPSHOT)

    history = next(section for section in context.sections if section.name == HISTORY)
    assert len(history.body.splitlines()) == FOCUSED.history_turns
    assert "word14" in history.body and "word0" not in history.body


async def test_reactive_context_carries_no_conversation_history(
    parts: Parts,
) -> None:
    builder, turns = parts.builder, parts.turns
    await _with_history(turns, 5)

    context = await builder.build(AttentionTier.REACTIVE, TRIGGERING_TURN, SNAPSHOT)

    assert HISTORY not in {section.name for section in context.sections}
    assert TRIGGERING_TURN.text in dict(
        (section.name, section.body) for section in context.sections
    )[TRIGGER]
    assert context.output_token_limit == 40


async def test_context_is_truncated_to_the_input_budget_from_the_far_end(
    parts: Parts,
) -> None:
    builder, turns = parts.builder, parts.turns
    await _with_history(turns, 8, words=400)

    context = await builder.build(AttentionTier.FOCUSED, TRIGGERING_TURN, SNAPSHOT)

    kept = [section.name for section in context.sections]
    history = next(
        (section for section in context.sections if section.name == HISTORY), None
    )

    assert context.estimated_input_tokens <= FOCUSED.input_tokens
    assert kept[:2] == [OUTPUT_CONTRACT, TRIGGER]
    assert history is not None, "whatever still fits is kept"
    assert len(history.body.splitlines()) < 8, "the oldest turns are shed first"


async def test_a_trigger_larger_than_the_budget_is_clipped_to_fit_it(
    parts: Parts,
) -> None:
    builder = parts.builder
    enormous = validate_conversation_turn(conversation_turn(text="x" * 40_000))

    context = await builder.build(AttentionTier.REACTIVE, enormous, SNAPSHOT)

    assert [section.name for section in context.sections] == [OUTPUT_CONTRACT, TRIGGER]
    assert context.estimated_input_tokens <= REACTIVE.input_tokens
    assert context.sections[1].body.endswith("…")


async def test_a_turn_during_an_active_event_carries_it_between_profile_and_world(
    parts: Parts,
) -> None:
    await parts.activate(THEFT, frozenset())
    await _with_history(parts.turns, 2)

    context = await parts.builder.build(AttentionTier.FOCUSED, TRIGGERING_TURN, SNAPSHOT)

    assert [section.name for section in context.sections] == [
        OUTPUT_CONTRACT,
        TRIGGER,
        PROFILE,
        EVENT,
        WORLD,
        HISTORY,
    ]
    assert _body(context, EVENT) == describe_event(THEFT, (ROLE_TARGET,))


async def test_a_turn_with_no_active_event_leaves_the_other_sections_untouched(
    parts: Parts,
) -> None:
    await _with_history(parts.turns, 2)

    without = await parts.builder.build(AttentionTier.FOCUSED, TRIGGERING_TURN, SNAPSHOT)
    await parts.activate(THEFT, frozenset())
    with_event = await parts.builder.build(
        AttentionTier.FOCUSED, TRIGGERING_TURN, SNAPSHOT
    )

    assert [section.name for section in without.sections] == [
        OUTPUT_CONTRACT,
        TRIGGER,
        PROFILE,
        WORLD,
        HISTORY,
    ]
    assert without.sections == tuple(
        section for section in with_event.sections if section.name != EVENT
    ), "the event slot is the only difference an active event makes"


@pytest.mark.parametrize("tier", [AttentionTier.FOCUSED, AttentionTier.REACTIVE])
async def test_the_event_slot_holds_its_place_for_both_tiers(
    parts: Parts, tier: AttentionTier
) -> None:
    await parts.activate(THEFT, frozenset())

    context = await parts.builder.build(tier, TRIGGERING_TURN, SNAPSHOT)

    assert [section.name for section in context.sections][:5] == [
        OUTPUT_CONTRACT,
        TRIGGER,
        PROFILE,
        EVENT,
        WORLD,
    ]


async def test_the_event_described_is_the_one_the_target_is_most_caught_up_in(
    parts: Parts,
) -> None:
    # Ordered by when each began, the shopkeeper is a witness, then the victim, then a
    # responder — so the strongest role is neither the first nor the last event active.
    await parts.activate(BRAWL, frozenset({SHOPKEEPER}))
    await parts.activate(THEFT, frozenset())
    await parts.activate(FIRE, frozenset())

    context = await parts.builder.build(AttentionTier.FOCUSED, TRIGGERING_TURN, SNAPSHOT)

    assert _body(context, EVENT) == describe_event(THEFT, (ROLE_TARGET,))
    assert _body(context, EVENT) != describe_event(BRAWL, (ROLE_WITNESS,))
    assert _body(context, EVENT) != describe_event(FIRE, (ROLE_RESPONDER,))


async def test_several_active_events_still_produce_one_reproducible_context(
    parts: Parts,
) -> None:
    await parts.activate(BRAWL, frozenset({SHOPKEEPER}))
    await parts.activate(THEFT, frozenset())
    await parts.activate(FIRE, frozenset())

    first = await parts.builder.build(AttentionTier.FOCUSED, TRIGGERING_TURN, SNAPSHOT)
    second = await parts.builder.build(AttentionTier.FOCUSED, TRIGGERING_TURN, SNAPSHOT)

    assert first == second


async def test_the_event_section_is_counted_and_shed_like_every_other_optional_one(
    parts: Parts,
) -> None:
    await parts.activate(THEFT, frozenset())
    await _with_history(parts.turns, 2)
    roomy = await parts.builder.build(AttentionTier.FOCUSED, TRIGGERING_TURN, SNAPSHOT)
    kept = [OUTPUT_CONTRACT, TRIGGER, PROFILE]
    without_event = sum(
        estimate_tokens(_body(roomy, name), CHARACTERS_PER_TOKEN) for name in kept
    )

    assert roomy.estimated_input_tokens > without_event, "the event tokens are counted"

    tight = await parts.with_input_tokens(without_event).build(
        AttentionTier.FOCUSED, TRIGGERING_TURN, SNAPSHOT
    )

    assert [section.name for section in tight.sections] == kept
    assert tight.estimated_input_tokens <= without_event


async def test_a_victim_the_snapshot_has_stopped_observing_still_gets_its_event(
    parts: Parts,
) -> None:
    # Generation re-reads the current snapshot, so the target can leave the candidate set
    # between routing and building. Being robbed is a fact about the theft, not about where
    # the victim is standing now.
    await parts.activate(THEFT, frozenset())
    departed = validate_world_snapshot(
        world_snapshot(npcs=[npc(THIEF)], candidate_count=1)
    )

    context = await parts.builder.build(AttentionTier.FOCUSED, TRIGGERING_TURN, departed)

    assert _body(context, EVENT) == describe_event(THEFT, (ROLE_TARGET,))
    assert WORLD not in {section.name for section in context.sections}
