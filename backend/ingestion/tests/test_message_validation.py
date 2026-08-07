"""Owner: Jerome & Richard"""

from __future__ import annotations

import re
from typing import Any

import pytest

from backend.ingestion.message_validation import (
    MessageValidationError,
    validate_conversation_turn,
    validate_game_event,
    validate_world_snapshot,
)
from backend.ingestion.tests.canonical_messages import (
    CONVERSATION_ID,
    EVENT_ID,
    EVENT_MESSAGE_ID,
    EVENT_TIMESTAMP_MS,
    GUARD,
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
    game_event,
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
def test_accepts_values_no_shared_document_bounds(field: str, value: Any) -> None:
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
def test_rejects_unsupported_versions_types_and_ordering(field: str, value: Any) -> None:
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


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"session_id": ""}, "session_id"),
        ({"world_id": ""}, "world_id"),
        ({"player": world_snapshot()["player"] | {"player_id": ""}}, "player.player_id"),
        (
            {"active_conversation": active_conversation() | {"conversation_id": ""}},
            "active_conversation.conversation_id",
        ),
        (
            {"active_conversation": active_conversation(target_npc_id="")},
            "active_conversation.target_npc_id",
        ),
        ({"attention_edges": [attention_edge(source="")]}, "attention_edges.0.source_npc_id"),
        ({"attention_edges": [attention_edge(target="")]}, "attention_edges.0.target_npc_id"),
    ],
)
def test_rejects_an_empty_identifier_in_a_world_snapshot(
    overrides: dict[str, Any], field: str
) -> None:
    """The documented rule covers every identifier, not only the NPC ones.

    The error is matched by field, because the candidate-set rule would reject an empty
    conversation target or edge endpoint for a different reason and a bare `raises` could not
    tell the two apart.
    """
    with pytest.raises(MessageValidationError, match=rf"{re.escape(field)}: "):
        validate_world_snapshot(world_snapshot(**overrides))


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


def test_accepts_the_team_sent_game_event_payload() -> None:
    event = validate_game_event(game_event())

    assert event.message_type == "game_event"
    assert event.session_id == SESSION_ID
    assert event.message_id == EVENT_MESSAGE_ID
    assert event.event_id == EVENT_ID
    assert event.event_revision == 1
    assert event.timestamp_ms == EVENT_TIMESTAMP_MS
    assert event.event_type == "market_theft"
    assert event.status == "started"
    assert event.position.x == 104.2
    assert event.position.z == -31.8
    assert event.actor_npc_ids == [THIEF]
    assert event.target_npc_ids == [SHOPKEEPER]
    assert event.responder_npc_ids == [GUARD]


@pytest.mark.parametrize("status", ["started", "updated", "ended", "cancelled"])
def test_accepts_every_documented_lifecycle_status(status: str) -> None:
    event = validate_game_event(game_event(status=status))

    assert event.status == status
    assert event.is_terminal is (status in {"ended", "cancelled"})


@pytest.mark.parametrize(
    "overrides",
    [
        {"schema_version": "2.0"},
        {"message_type": "game.event"},
        {"session_id": ""},
        {"message_id": ""},
        {"event_id": ""},
        {"event_type": ""},
        {"event_revision": 0},
        {"event_revision": -1},
        {"event_revision": "1"},
        {"timestamp_ms": -1},
        {"position": None},
        {"actor_npc_ids": "thief-uuid"},
        {"unexpected": "field"},
    ],
)
def test_rejects_an_event_that_leaves_the_canonical_boundary(
    overrides: dict[str, Any],
) -> None:
    with pytest.raises(MessageValidationError):
        validate_game_event(game_event(**overrides))


@pytest.mark.parametrize(
    "missing",
    [
        "message_id",
        "event_id",
        "event_revision",
        "timestamp_ms",
        "event_type",
        "status",
        "position",
        "actor_npc_ids",
        "target_npc_ids",
        "responder_npc_ids",
    ],
)
def test_rejects_an_event_that_omits_a_documented_field(missing: str) -> None:
    payload = game_event()
    del payload[missing]

    with pytest.raises(MessageValidationError):
        validate_game_event(payload)


def test_rejects_a_lifecycle_status_with_no_defined_behaviour() -> None:
    """Specification #1 fixes the four statuses; an unknown one has no lifecycle rule (#2)."""
    with pytest.raises(MessageValidationError):
        validate_game_event(game_event(status="paused"))


def test_event_role_membership_and_event_type_stay_open() -> None:
    """No source bounds the role arrays or the event vocabulary, so neither is invented (#2)."""
    empty = validate_game_event(
        game_event(actor_npc_ids=[], target_npc_ids=[], responder_npc_ids=[])
    )
    assert empty.actor_npc_ids == []

    both = validate_game_event(game_event(actor_npc_ids=[THIEF], responder_npc_ids=[THIEF]))
    assert both.actor_npc_ids == both.responder_npc_ids == [THIEF]

    assert validate_game_event(game_event(event_type="lute_recital")).event_type == "lute_recital"


@pytest.mark.parametrize("role", ["actor_npc_ids", "target_npc_ids", "responder_npc_ids"])
def test_rejects_an_empty_npc_identifier_in_an_event_role(role: str) -> None:
    with pytest.raises(MessageValidationError, match=rf"{role}\.0: "):
        validate_game_event(game_event(1, **{role: [""]}))


def test_rejects_an_empty_session_id_on_every_inbound_message() -> None:
    """The session id keys every durable store, so an empty one must never get that far."""
    for validate, payload in (
        (validate_world_snapshot, world_snapshot(session_id="")),
        (validate_game_event, game_event(session_id="")),
        (validate_conversation_turn, conversation_turn(session_id="")),
    ):
        with pytest.raises(MessageValidationError, match="session_id: "):
            validate(payload)


def test_an_event_may_reference_an_npc_that_is_not_a_current_candidate() -> None:
    """The candidate set is a radius selection; an actor may be outside it."""
    event = validate_game_event(game_event(actor_npc_ids=["stranger-uuid"]))

    assert event.actor_npc_ids == ["stranger-uuid"]
