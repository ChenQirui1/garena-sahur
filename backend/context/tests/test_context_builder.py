"""Owner: Jerome & Richard"""

from __future__ import annotations

from pathlib import Path
from typing import AsyncIterator

import pytest_asyncio

from backend.context.context_builder import (
    HISTORY,
    OUTPUT_CONTRACT,
    PROFILE,
    TRIGGER,
    WORLD,
    ContextBuilder,
    ContextLimits,
)
from backend.context.conversation_history import ConversationHistory
from backend.context.npc_profiles import NpcProfiles
from backend.ingestion.durable_store import DurableStore
from backend.ingestion.message_validation import (
    validate_conversation_turn,
    validate_world_snapshot,
)
from backend.ingestion.tests.canonical_messages import conversation_turn, world_snapshot
from backend.ingestion.turn_store import TurnStore
from backend.models.token_estimate import estimate_tokens
from backend.orchestration.router_port import AttentionTier

CHARACTERS_PER_TOKEN = 4
FOCUSED = ContextLimits(input_tokens=2_000, output_tokens=120, history_turns=8)
REACTIVE = ContextLimits(input_tokens=600, output_tokens=40, history_turns=0)

TRIGGERING_TURN = validate_conversation_turn(conversation_turn(turn_index=20))
SNAPSHOT = validate_world_snapshot(world_snapshot())


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


@pytest_asyncio.fixture
async def parts(tmp_path: Path) -> AsyncIterator[tuple[ContextBuilder, TurnStore]]:
    store = DurableStore(tmp_path / "spotlight.sqlite3")
    await store.open()
    turns = TurnStore(store)
    builder = ContextBuilder(
        profiles=NpcProfiles.empty(),
        history=ConversationHistory(turns),
        focused=FOCUSED,
        reactive=REACTIVE,
        characters_per_token=CHARACTERS_PER_TOKEN,
    )
    try:
        yield builder, turns
    finally:
        await store.close()


def test_the_token_estimate_is_a_ceiling_not_a_floor() -> None:
    assert estimate_tokens("", 4) == 0
    assert estimate_tokens("abc", 4) == 1
    assert estimate_tokens("abcd", 4) == 1
    assert estimate_tokens("abcde", 4) == 2


async def test_focused_context_is_priority_ordered(
    parts: tuple[ContextBuilder, TurnStore],
) -> None:
    builder, turns = parts
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
    parts: tuple[ContextBuilder, TurnStore],
) -> None:
    builder, turns = parts
    await _with_history(turns, 3)

    first = await builder.build(AttentionTier.FOCUSED, TRIGGERING_TURN, SNAPSHOT)
    second = await builder.build(AttentionTier.FOCUSED, TRIGGERING_TURN, SNAPSHOT)

    assert first == second


async def test_focused_history_stops_at_the_permitted_number_of_turns(
    parts: tuple[ContextBuilder, TurnStore],
) -> None:
    builder, turns = parts
    await _with_history(turns, 15)

    context = await builder.build(AttentionTier.FOCUSED, TRIGGERING_TURN, SNAPSHOT)

    history = next(section for section in context.sections if section.name == HISTORY)
    assert len(history.body.splitlines()) == FOCUSED.history_turns
    assert "word14" in history.body and "word0" not in history.body


async def test_reactive_context_carries_no_conversation_history(
    parts: tuple[ContextBuilder, TurnStore],
) -> None:
    builder, turns = parts
    await _with_history(turns, 5)

    context = await builder.build(AttentionTier.REACTIVE, TRIGGERING_TURN, SNAPSHOT)

    assert HISTORY not in {section.name for section in context.sections}
    assert TRIGGERING_TURN.text in dict(
        (section.name, section.body) for section in context.sections
    )[TRIGGER]
    assert context.output_token_limit == 40


async def test_context_is_truncated_to_the_input_budget_from_the_far_end(
    parts: tuple[ContextBuilder, TurnStore],
) -> None:
    builder, turns = parts
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
    parts: tuple[ContextBuilder, TurnStore],
) -> None:
    builder, _ = parts
    enormous = validate_conversation_turn(conversation_turn(text="x" * 40_000))

    context = await builder.build(AttentionTier.REACTIVE, enormous, SNAPSHOT)

    assert [section.name for section in context.sections] == [OUTPUT_CONTRACT, TRIGGER]
    assert context.estimated_input_tokens <= REACTIVE.input_tokens
    assert context.sections[1].body.endswith("…")
