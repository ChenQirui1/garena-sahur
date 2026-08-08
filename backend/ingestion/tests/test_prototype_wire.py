"""The shipped mod's payloads, translated into canonical messages.

Owner: Jerome & Richard

Every case ends at a canonical validator rather than at a dictionary comparison: the point of
this adapter is that what leaves it satisfies `docs/message_schemas.md`, and a hand-written
expected dictionary would only prove the translation agrees with the test.

Key sets are read from the tracked document through `backend.tests.tracked_documents` for the
same reason — a list of expected field names here would be a second source of truth.
"""

from __future__ import annotations

import math
from typing import Any

import pytest

from backend.ingestion.message_validation import (
    SCHEMA_VERSION,
    TOPIC_CONVERSATION_TURN,
    TOPIC_GAME_EVENT,
    TOPIC_WORLD_SNAPSHOT,
    validate_conversation_turn,
    validate_game_event,
    validate_world_snapshot,
)
from backend.ingestion.prototype_wire import (
    PrototypeDefaults,
    PrototypeTranslationError,
    PrototypeWire,
)
from backend.ingestion.tests import canonical_messages, prototype_messages
from backend.ingestion.tests.canonical_messages import SHOPKEEPER, THIEF
from backend.ingestion.tests.prototype_messages import (
    CONVERSATION_ID,
    EVENT_ID,
    LOOK,
    PLAYER_ID,
    PLAYER_NAME,
    PLAYER_POSITION,
    SESSION_ID,
    SHOPKEEPER_POSITION,
    TURN_ID,
)
from backend.tests.tracked_documents import documented_keys

WORLD_SNAPSHOT = "1. `world.snapshot`"
GAME_EVENT = "2. `game.event`"
CONVERSATION_TURN = "3. `conversation.turn`"

WORLD_ID = "minecraft-overworld-demo"
ENTRY_RADIUS = 24.0
EXIT_RADIUS = 28.0


@pytest.fixture
def wire() -> PrototypeWire:
    return PrototypeWire(
        PrototypeDefaults(
            world_id=WORLD_ID,
            entry_radius_blocks=ENTRY_RADIUS,
            exit_radius_blocks=EXIT_RADIUS,
        )
    )


def snapshot(wire: PrototypeWire, **overrides: Any) -> dict[str, Any]:
    translated = wire.translate(prototype_messages.world_snapshot(**overrides))
    assert translated.topic == TOPIC_WORLD_SNAPSHOT
    return translated.message


def event(wire: PrototypeWire, **overrides: Any) -> dict[str, Any]:
    translated = wire.translate(prototype_messages.game_event(**overrides))
    assert translated.topic == TOPIC_GAME_EVENT
    return translated.message


def turn(wire: PrototypeWire, **overrides: Any) -> dict[str, Any]:
    translated = wire.translate(prototype_messages.conversation_turn(**overrides))
    assert translated.topic == TOPIC_CONVERSATION_TURN
    return translated.message


def test_the_translated_snapshot_is_a_canonical_snapshot(wire: PrototypeWire) -> None:
    accepted = validate_world_snapshot(snapshot(wire))

    assert accepted.schema_version == SCHEMA_VERSION
    assert accepted.session_id == SESSION_ID
    assert accepted.world_id == WORLD_ID
    assert accepted.sequence == 1842
    assert accepted.timestamp_ms == prototype_messages.SNAPSHOT_TIMESTAMP_MS


def test_the_snapshot_carries_the_documented_fields_and_no_prototype_extras(
    wire: PrototypeWire,
) -> None:
    """`level`, `health`, `activity`, `distance` and the rest are dropped. `profession` is not:
    it is the key a persona is resolved on, and it is carried as the declared extension #58
    added to the observation."""
    translated = snapshot(wire)

    assert set(translated) == documented_keys(WORLD_SNAPSHOT)
    assert set(translated["player"]) == set(canonical_messages.world_snapshot()["player"])
    for observation in translated["npcs"]:
        assert set(observation) == set(canonical_messages.npc(SHOPKEEPER)) | {"profession"}


def test_the_observed_profession_survives_translation(wire: PrototypeWire) -> None:
    """Without this the demo cannot resolve a persona at all: the mod's UUIDs are world-random,
    so the profession is the only thing about a villager an authored document can name."""
    translated = snapshot(wire)

    assert [observation["profession"] for observation in translated["npcs"]] == [
        "Farmer",
        "Farmer",
    ]


def test_a_villager_published_without_a_profession_is_still_a_candidate(
    wire: PrototypeWire,
) -> None:
    """The mod always sends one, but nothing in a tracked source obliges it to (#2), and losing
    a candidate over an absent persona key would be our invention, not its rule."""
    payload = prototype_messages.world_snapshot()
    del payload["npcs"][0]["profession"]

    translated = wire.translate(payload)

    assert translated.message["npcs"][0]["profession"] is None


def test_the_candidate_count_and_policy_are_synthesized(wire: PrototypeWire) -> None:
    accepted = validate_world_snapshot(snapshot(wire))

    assert accepted.candidate_count == len(accepted.npcs) == 2
    assert accepted.candidate_policy.entry_radius_blocks == ENTRY_RADIUS
    assert accepted.candidate_policy.exit_radius_blocks == EXIT_RADIUS


def test_edges_are_empty_because_the_mod_models_none(wire: PrototypeWire) -> None:
    assert validate_world_snapshot(snapshot(wire)).attention_edges == []


def test_look_direction_follows_the_mods_own_convention(wire: PrototypeWire) -> None:
    """`ViewportMath.getLookDirection`: x=-sin(yaw)cos(pitch), y=-sin(pitch), z=cos(yaw)cos(pitch).

    Yaw and pitch are chosen so the three components are pairwise different: under a sign slip
    or a swapped axis the vector stays unit-length and every other assertion still passes.
    """
    yaw, pitch = math.radians(LOOK["yaw"]), math.radians(LOOK["pitch"])
    direction = validate_world_snapshot(snapshot(wire)).player.look_direction

    assert direction.x == pytest.approx(-math.sin(yaw) * math.cos(pitch))
    assert direction.y == pytest.approx(-math.sin(pitch))
    assert direction.z == pytest.approx(math.cos(yaw) * math.cos(pitch))
    assert math.hypot(direction.x, direction.y, direction.z) == pytest.approx(1.0)


def test_the_observation_fields_the_mod_already_publishes_are_carried(
    wire: PrototypeWire,
) -> None:
    observed = validate_world_snapshot(snapshot(wire)).npcs[0]

    assert observed.npc_id == SHOPKEEPER
    assert (observed.position.x, observed.position.y, observed.position.z) == (
        SHOPKEEPER_POSITION["x"],
        SHOPKEEPER_POSITION["y"],
        SHOPKEEPER_POSITION["z"],
    )
    assert observed.world_distance_blocks == 3.4
    assert observed.viewport_center_distance == 0.07
    assert observed.inside_viewport is True
    assert observed.line_of_sight is True


def test_the_player_id_comes_from_the_prototype_uuid(wire: PrototypeWire) -> None:
    assert validate_world_snapshot(snapshot(wire)).player.player_id == PLAYER_ID


def test_no_conversation_is_active_before_the_mod_announces_one(
    wire: PrototypeWire,
) -> None:
    assert validate_world_snapshot(snapshot(wire)).active_conversation is None


def test_a_turn_makes_its_conversation_active_on_the_next_snapshot(
    wire: PrototypeWire,
) -> None:
    """The mod announces a conversation only in its turn stream (#2 A7).

    `ConversationPublisher` holds the same state internally and never serialises it, so this
    mirrors the mod rather than inventing a conversation.
    """
    snapshot(wire)
    turn(wire)
    active = validate_world_snapshot(snapshot(wire, sequence=1843)).active_conversation

    assert active is not None
    assert active.conversation_id == CONVERSATION_ID
    assert active.target_npc_id == SHOPKEEPER


def test_the_conversation_is_inactive_while_its_target_is_not_a_candidate(
    wire: PrototypeWire,
) -> None:
    """§1 requires the target to appear in `npcs`, and the mod publishes no conversation end."""
    snapshot(wire)
    turn(wire)
    without_target = snapshot(
        wire, sequence=1843, npcs=[prototype_messages.npc(THIEF)]
    )

    assert validate_world_snapshot(without_target).active_conversation is None


def test_a_returning_target_makes_the_conversation_active_again(
    wire: PrototypeWire,
) -> None:
    """The mod's conversation ends on nothing but a switch, so neither does ours."""
    snapshot(wire)
    turn(wire)
    snapshot(wire, sequence=1843, npcs=[prototype_messages.npc(THIEF)])
    returned = validate_world_snapshot(snapshot(wire, sequence=1844))

    assert returned.active_conversation is not None
    assert returned.active_conversation.target_npc_id == SHOPKEEPER


def test_talking_to_another_npc_switches_the_active_conversation(
    wire: PrototypeWire,
) -> None:
    snapshot(wire)
    turn(wire)
    turn(wire, npc_uuid=THIEF, conversation_id="other-conversation", turn_id="other-turn")
    active = validate_world_snapshot(snapshot(wire, sequence=1843)).active_conversation

    assert active is not None
    assert (active.conversation_id, active.target_npc_id) == ("other-conversation", THIEF)


def test_the_translated_event_is_a_canonical_event(wire: PrototypeWire) -> None:
    snapshot(wire)
    accepted = validate_game_event(event(wire))

    assert accepted.session_id == SESSION_ID
    assert accepted.event_id == EVENT_ID
    assert accepted.event_type == "villager_attacked"
    assert accepted.timestamp_ms == prototype_messages.EVENT_TIMESTAMP_MS


def test_the_event_carries_the_documented_fields_and_no_prototype_extras(
    wire: PrototypeWire,
) -> None:
    """`details` and the transport `sequence` are dropped; §2 has neither."""
    snapshot(wire)

    assert set(event(wire)) == documented_keys(GAME_EVENT)


def test_an_event_is_its_first_revision_and_has_started(wire: PrototypeWire) -> None:
    """The mod mints a fresh `event_id` per publish and never revises, so this is exact."""
    snapshot(wire)
    accepted = validate_game_event(event(wire))

    assert accepted.event_revision == 1
    assert accepted.status == "started"
    assert accepted.is_terminal is False


def test_a_redelivered_event_keeps_one_delivery_identity(wire: PrototypeWire) -> None:
    snapshot(wire)
    once, again = event(wire), event(wire)

    assert once["message_id"] == again["message_id"]
    assert once["message_id"] != event(wire, event_id="another-event")["message_id"]


def test_the_event_roles_come_from_the_prototype_participants(
    wire: PrototypeWire,
) -> None:
    """`villager_attacked` names the player as actor; carrying it loses nothing and
    invents nothing, because no NPC holds that identity."""
    snapshot(wire)
    accepted = validate_game_event(event(wire))

    assert accepted.actor_npc_ids == [PLAYER_ID]
    assert accepted.target_npc_ids == [SHOPKEEPER]
    assert accepted.responder_npc_ids == []


def test_an_event_without_a_target_carries_an_empty_target_role(
    wire: PrototypeWire,
) -> None:
    snapshot(wire)
    accepted = validate_game_event(event(wire, target_uuid=None))

    assert accepted.target_npc_ids == []


def test_the_event_position_is_the_targets_last_observed_position(
    wire: PrototypeWire,
) -> None:
    """Witness membership is measured from this point, so it has to be a real one."""
    snapshot(wire)
    position = validate_game_event(event(wire)).position

    assert (position.x, position.y, position.z) == (
        SHOPKEEPER_POSITION["x"],
        SHOPKEEPER_POSITION["y"],
        SHOPKEEPER_POSITION["z"],
    )


def test_an_event_between_unobserved_participants_happens_at_the_player(
    wire: PrototypeWire,
) -> None:
    snapshot(wire)
    position = validate_game_event(
        event(wire, actor_uuid=PLAYER_ID, target_uuid="a-stranger")
    ).position

    assert (position.x, position.y, position.z) == (
        PLAYER_POSITION["x"],
        PLAYER_POSITION["y"],
        PLAYER_POSITION["z"],
    )


def test_an_event_before_any_snapshot_is_refused_rather_than_placed_at_the_origin(
    wire: PrototypeWire,
) -> None:
    """An invented position would put every witness out of range and read as a valid event."""
    with pytest.raises(PrototypeTranslationError, match="world snapshot"):
        event(wire)


def test_the_translated_turn_is_a_canonical_turn(wire: PrototypeWire) -> None:
    snapshot(wire)
    accepted = validate_conversation_turn(turn(wire))

    assert accepted.session_id == SESSION_ID
    assert accepted.conversation_id == CONVERSATION_ID
    assert accepted.turn_id == TURN_ID
    assert accepted.timestamp_ms == prototype_messages.TURN_TIMESTAMP_MS


def test_the_turn_carries_the_documented_fields_and_no_prototype_extras(
    wire: PrototypeWire,
) -> None:
    """`speaker_name`, `conversation_start` and the transport `sequence` are dropped."""
    snapshot(wire)

    assert set(turn(wire)) == documented_keys(CONVERSATION_TURN)


def test_the_turn_is_renamed_field_by_field(wire: PrototypeWire) -> None:
    snapshot(wire)
    accepted = validate_conversation_turn(turn(wire))

    assert accepted.turn_index == 1
    assert accepted.speaker_type == "player"
    assert accepted.target_npc_id == SHOPKEEPER
    assert accepted.text == "Which direction did the thief run?"
    assert accepted.is_player_turn is True


def test_the_speaker_is_the_observed_player_rather_than_their_display_name(
    wire: PrototypeWire,
) -> None:
    """§3 wants a stable `speaker_id`; the mod sends a display name and a player UUID."""
    snapshot(wire)

    assert validate_conversation_turn(turn(wire)).speaker_id == PLAYER_ID


def test_the_display_name_identifies_the_speaker_until_a_player_is_observed(
    wire: PrototypeWire,
) -> None:
    """Rejecting the turn instead would lose the first thing the player ever said."""
    assert validate_conversation_turn(turn(wire)).speaker_id == PLAYER_NAME


def test_a_canonical_payload_is_routed_unchanged(wire: PrototypeWire) -> None:
    """The endpoint is also how a canonical publisher reaches us, so it must not translate."""
    canonical = canonical_messages.world_snapshot()

    translated = wire.translate(dict(canonical))

    assert translated.topic == TOPIC_WORLD_SNAPSHOT
    assert translated.message == canonical


def test_a_payload_naming_no_message_type_is_refused(wire: PrototypeWire) -> None:
    with pytest.raises(PrototypeTranslationError, match="type"):
        wire.translate({"session_id": SESSION_ID, "sequence": 1})


def test_a_payload_of_an_unknown_type_is_refused(wire: PrototypeWire) -> None:
    with pytest.raises(PrototypeTranslationError, match="world.weather"):
        wire.translate({"type": "world.weather", "session_id": SESSION_ID})


def test_a_payload_that_is_not_an_object_is_refused(wire: PrototypeWire) -> None:
    with pytest.raises(PrototypeTranslationError):
        wire.translate(["world_snapshot"])


def test_a_snapshot_without_a_session_is_refused(wire: PrototypeWire) -> None:
    with pytest.raises(PrototypeTranslationError, match="session_id"):
        wire.translate(prototype_messages.world_snapshot(session_id=None))


def test_sessions_do_not_share_their_synthesized_state(wire: PrototypeWire) -> None:
    """One backend serves one mod today, but nothing in the adapter may assume it."""
    snapshot(wire)
    turn(wire)
    other = snapshot(wire, sequence=1, session_id="another-session")

    assert validate_world_snapshot(other).active_conversation is None
