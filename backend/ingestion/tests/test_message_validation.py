"""Owner: Jerome & Richard"""

from __future__ import annotations

import pytest

from backend.ingestion.message_validation import MessageValidationError, validate_world_snapshot
from backend.ingestion.tests.canonical_messages import (
    SHOPKEEPER,
    THIEF,
    candidate,
    world_snapshot,
)


def test_accepts_a_complete_schema_version_1_0_snapshot() -> None:
    snapshot = validate_world_snapshot(
        world_snapshot(
            active_conversation={"conversation_id": "conversation-07", "npc_id": SHOPKEEPER},
            attention_edges=[
                {"source_npc_id": THIEF, "target_npc_id": SHOPKEEPER, "relation": "watching"}
            ],
        )
    )

    assert snapshot.sequence == 1
    assert [entry.npc_id for entry in snapshot.candidates] == [SHOPKEEPER, THIEF]
    assert snapshot.active_conversation is not None
    assert snapshot.active_conversation.npc_id == SHOPKEEPER
    assert snapshot.attention_edges[0].relation == "watching"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", "2.0"),
        ("type", "game_event"),
        ("session_id", ""),
        ("session_id", "demo 01"),
        ("world_id", "over/world"),
        ("sequence", 0),
        ("sequence", "1842"),
        ("observed_at_ms", 1_786_208_500.5),
        ("observed_at_ms", 999_999_999_999),
        ("candidates", []),
    ],
)
def test_rejects_invalid_identifiers_timestamps_and_ordering(field: str, value: object) -> None:
    with pytest.raises(MessageValidationError):
        validate_world_snapshot(world_snapshot(**{field: value}))


def test_rejects_unknown_top_level_and_payload_fields() -> None:
    with pytest.raises(MessageValidationError):
        validate_world_snapshot(world_snapshot(tick_rate=20))

    with pytest.raises(MessageValidationError):
        validate_world_snapshot(
            world_snapshot(candidates=[candidate(SHOPKEEPER, event_relevance=1.0)])
        )


def test_rejects_out_of_range_observations() -> None:
    with pytest.raises(MessageValidationError):
        validate_world_snapshot(
            world_snapshot(candidates=[candidate(SHOPKEEPER, world_distance=-1.0)])
        )

    with pytest.raises(MessageValidationError):
        validate_world_snapshot(
            world_snapshot(candidates=[candidate(SHOPKEEPER, viewport_center_distance=1.5)])
        )


def test_rejects_duplicate_candidates() -> None:
    with pytest.raises(MessageValidationError):
        validate_world_snapshot(
            world_snapshot(candidates=[candidate(SHOPKEEPER), candidate(SHOPKEEPER)])
        )


def test_rejects_an_active_conversation_outside_the_candidate_set() -> None:
    with pytest.raises(MessageValidationError):
        validate_world_snapshot(
            world_snapshot(
                active_conversation={"conversation_id": "conversation-07", "npc_id": "stranger"}
            )
        )


@pytest.mark.parametrize(
    "edges",
    [
        [{"source_npc_id": SHOPKEEPER, "target_npc_id": "stranger", "relation": "watching"}],
        [{"source_npc_id": SHOPKEEPER, "target_npc_id": SHOPKEEPER, "relation": "watching"}],
        [
            {"source_npc_id": THIEF, "target_npc_id": SHOPKEEPER, "relation": "watching"},
            {"source_npc_id": THIEF, "target_npc_id": SHOPKEEPER, "relation": "watching"},
        ],
    ],
)
def test_rejects_unresolvable_self_and_duplicate_attention_edges(edges: list[dict]) -> None:
    with pytest.raises(MessageValidationError):
        validate_world_snapshot(world_snapshot(attention_edges=edges))


def test_error_names_the_offending_field() -> None:
    with pytest.raises(MessageValidationError, match="sequence"):
        validate_world_snapshot(world_snapshot(sequence=0))
