"""Owner: Jerome & Richard"""

from __future__ import annotations

from typing import Any

import pytest

from backend.ingestion.message_validation import (
    MessageValidationError,
    validate_conversation_turn,
    validate_world_snapshot,
)
from backend.ingestion.tests.canonical_messages import (
    CONVERSATION_ID,
    PLAYER_ID,
    SESSION_ID,
    SHOPKEEPER,
    THIEF,
    TIMESTAMP_MS,
    TURN_ID,
    TURN_TIMESTAMP_MS,
    active_conversation,
    attention_edge,
    conversation_turn,
    npc,
    world_snapshot,
)


def test_accepts_the_team_sent_world_snapshot_payload() -> None:
    snapshot = validate_world_snapshot(
        world_snapshot(
            active_conversation=active_conversation(),
            attention_edges=[attention_edge()],
        )
    )

    assert snapshot.message_type == "world_snapshot"
    assert snapshot.sequence == 1842
    assert snapshot.timestamp_ms == TIMESTAMP_MS
    assert snapshot.candidate_policy.entry_radius_blocks == 24.0
    assert snapshot.candidate_policy.exit_radius_blocks == 28.0
    assert snapshot.player.player_id == "player-uuid"
    assert snapshot.player.look_direction.z == 0.69
    assert snapshot.candidate_count == 2
    assert [entry.npc_id for entry in snapshot.npcs] == [SHOPKEEPER, THIEF]
    assert snapshot.npcs[0].position.x == 108.1
    assert snapshot.npcs[0].world_distance_blocks == 3.4
    assert snapshot.npcs[0].inside_viewport is True
    assert snapshot.active_conversation is not None
    assert snapshot.active_conversation.conversation_id == CONVERSATION_ID
    assert snapshot.active_conversation.target_npc_id == SHOPKEEPER
    assert snapshot.attention_edges[0].kind == "gaze"
    assert snapshot.attention_edges[0].active is True


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("sequence", 0),
        ("npcs", []),
        ("session_id", "demo 01"),
        ("world_id", "over/world"),
        ("timestamp_ms", 1),
    ],
    ids=["sequence-zero", "empty-candidate-set", "spaced-id", "slashed-id", "old-timestamp"],
)
def test_accepts_values_no_shared_document_bounds(field: str, value: object) -> None:
    payload = world_snapshot(**{field: value})
    if field == "npcs":
        payload["candidate_count"] = 0

    assert validate_world_snapshot(payload) is not None


def test_accepts_whole_numbers_written_without_a_decimal_point() -> None:
    snapshot = validate_world_snapshot(
        world_snapshot(
            candidate_policy={"entry_radius_blocks": 24, "exit_radius_blocks": 28},
            npcs=[npc(SHOPKEEPER, world_distance_blocks=3, viewport_center_distance=0)],
            candidate_count=1,
        )
    )

    assert snapshot.candidate_policy.entry_radius_blocks == 24
    assert snapshot.npcs[0].world_distance_blocks == 3


def test_accepts_an_unrecognised_attention_edge_kind() -> None:
    snapshot = validate_world_snapshot(
        world_snapshot(attention_edges=[attention_edge(kind="haggling")])
    )

    assert snapshot.attention_edges[0].kind == "haggling"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", "2.0"),
        ("message_type", "game_event"),
        ("sequence", -1),
        ("sequence", "1842"),
        ("timestamp_ms", 1_786_208_500.5),
        ("candidate_count", 3),
    ],
)
def test_rejects_unsupported_versions_types_and_ordering(field: str, value: object) -> None:
    with pytest.raises(MessageValidationError):
        validate_world_snapshot(world_snapshot(**{field: value}))


def test_rejects_unknown_top_level_and_payload_fields() -> None:
    with pytest.raises(MessageValidationError):
        validate_world_snapshot(world_snapshot(tick_rate=20))

    with pytest.raises(MessageValidationError):
        validate_world_snapshot(
            world_snapshot(npcs=[npc(SHOPKEEPER, event_relevance=1.0)], candidate_count=1)
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"world_distance_blocks": -1.0},
        {"viewport_center_distance": 1.5},
        {"viewport_center_distance": -0.1},
        {"npc_id": ""},
    ],
)
def test_rejects_observations_outside_their_documented_range(overrides: dict[str, Any]) -> None:
    with pytest.raises(MessageValidationError):
        validate_world_snapshot(
            world_snapshot(npcs=[npc(SHOPKEEPER) | overrides], candidate_count=1)
        )


@pytest.mark.parametrize(
    "policy",
    [
        {"entry_radius_blocks": 28.0, "exit_radius_blocks": 24.0},
        {"entry_radius_blocks": 24.0, "exit_radius_blocks": 24.0},
        {"entry_radius_blocks": -1.0, "exit_radius_blocks": 28.0},
    ],
)
def test_rejects_a_candidate_policy_that_would_flicker(policy: dict[str, float]) -> None:
    with pytest.raises(MessageValidationError):
        validate_world_snapshot(world_snapshot(candidate_policy=policy))


def test_rejects_duplicate_npc_ids() -> None:
    with pytest.raises(MessageValidationError):
        validate_world_snapshot(
            world_snapshot(npcs=[npc(SHOPKEEPER), npc(SHOPKEEPER)], candidate_count=2)
        )


def test_rejects_an_active_conversation_target_outside_the_candidate_set() -> None:
    with pytest.raises(MessageValidationError):
        validate_world_snapshot(
            world_snapshot(active_conversation=active_conversation(target_npc_id="stranger"))
        )


def test_rejects_an_attention_edge_referencing_a_non_candidate() -> None:
    with pytest.raises(MessageValidationError):
        validate_world_snapshot(
            world_snapshot(attention_edges=[attention_edge(target="stranger")])
        )


def test_error_names_the_offending_field() -> None:
    with pytest.raises(MessageValidationError, match="candidate_count"):
        validate_world_snapshot(world_snapshot(candidate_count=9))


def test_accepts_the_team_sent_conversation_turn_payload() -> None:
    turn = validate_conversation_turn(conversation_turn())

    assert turn.message_type == "conversation_turn"
    assert turn.session_id == SESSION_ID
    assert turn.conversation_id == CONVERSATION_ID
    assert turn.turn_id == TURN_ID
    assert turn.turn_index == 4
    assert turn.timestamp_ms == TURN_TIMESTAMP_MS
    assert turn.speaker_type == "player"
    assert turn.speaker_id == PLAYER_ID
    assert turn.target_npc_id == SHOPKEEPER
    assert turn.text == "Which direction did the thief run?"
    assert turn.is_player_turn


@pytest.mark.parametrize(
    "overrides",
    [
        {"schema_version": "2.0"},
        {"message_type": "conversation.turn"},
        {"session_id": ""},
        {"conversation_id": ""},
        {"turn_id": ""},
        {"target_npc_id": ""},
        {"speaker_type": ""},
        {"speaker_id": ""},
        {"turn_index": -1},
        {"turn_index": "4"},
        {"timestamp_ms": -1},
        {"text": 7},
        {"unexpected": "field"},
    ],
)
def test_rejects_a_turn_that_leaves_the_canonical_boundary(overrides: dict[str, Any]) -> None:
    with pytest.raises(MessageValidationError):
        validate_conversation_turn(conversation_turn(**overrides))


@pytest.mark.parametrize("missing", ["turn_id", "turn_index", "speaker_type", "text"])
def test_rejects_a_turn_that_omits_a_documented_field(missing: str) -> None:
    payload = conversation_turn()
    del payload[missing]

    with pytest.raises(MessageValidationError):
        validate_conversation_turn(payload)


def test_an_empty_turn_index_origin_and_text_length_stay_open() -> None:
    """No source fixes the index origin or a text limit, so neither is invented here (#2)."""
    assert validate_conversation_turn(conversation_turn(turn_index=0)).turn_index == 0
    assert validate_conversation_turn(conversation_turn(text="")).text == ""
    assert len(validate_conversation_turn(conversation_turn(text="x" * 20_000)).text) == 20_000


def test_a_non_player_speaker_is_stored_but_is_not_a_player_turn() -> None:
    turn = validate_conversation_turn(conversation_turn(speaker_type="npc"))

    assert turn.speaker_type == "npc"
    assert not turn.is_player_turn
