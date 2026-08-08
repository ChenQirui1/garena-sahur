"""Translate the shipped Fabric mod's prototype payloads into canonical messages.

Owner: Jerome & Richard

**Development only, and off unless configuration turns it on.** `docs/architecture.md` sanctions
development adapters, but requires that they "carry the same canonical payloads and do not change
the architecture" — the documented adapters change transport, not shape. This one changes shape,
which is a deliberate one-off departure recorded in ADR 0012 and taken so that nothing is required
of Ivan for the demo path. **It retires at issue #11**, when the mod publishes the canonical
envelope over the live transport.

Nothing prototype-shaped leaves this module: a translation is a canonical payload, handed to the
same validators and the same `IntakeService` that `/ingest` uses.

Every synthesis rule below follows from something confirmed rather than chosen — Ivan's wire
contract on issue #2, or the shipped mod at `1333971`. Three facts the mod keeps to itself and
does not serialise are mirrored here per session: the player's identity, where each candidate was
last seen, and which conversation is open. They are held in memory exactly as
`ConversationPublisher` and the candidate tracker hold them on the mod's side, and like the mod's
copies they outlive a backend session cleanup, because the mod is not told one happened.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Final

from backend.ingestion.message_validation import (
    EVENT_STATUS_STARTED,
    FIRST_EVENT_REVISION,
    SCHEMA_VERSION,
    SPEAKER_TYPE_PLAYER,
    TOPIC_CONVERSATION_TURN,
    TOPIC_GAME_EVENT,
    TOPIC_WORLD_SNAPSHOT,
)

# The mod's discriminator (`SnapshotBuilder`, `GameEventPublisher`, `ConversationPublisher`)
# against the canonical one. A payload carrying the canonical field is already canonical.
PROTOTYPE_TYPE = "type"
CANONICAL_TYPE = "message_type"

TOPIC_FOR_MESSAGE_TYPE: Final = {
    "world_snapshot": TOPIC_WORLD_SNAPSHOT,
    "game_event": TOPIC_GAME_EVENT,
    "conversation_turn": TOPIC_CONVERSATION_TURN,
}

# A delivery identity the mod does not send. The mod mints a fresh `event_id` per publish and
# never revises, so this is unique per publication and identical on a redelivery of the same one.
MESSAGE_ID_PREFIX = "prototype"


class PrototypeTranslationError(ValueError):
    """A prototype payload could not be turned into a canonical message."""


@dataclass(frozen=True, slots=True)
class PrototypeDefaults:
    """What §1 requires of every snapshot and the mod publishes in none of them.

    The radii are Ivan's confirmed `SpotlightConfig` constants (#2 A9) and the world is a
    single-world stand-in until the mod sends its dimension key (#2 A12).
    """

    world_id: str
    entry_radius_blocks: float
    exit_radius_blocks: float


@dataclass(frozen=True, slots=True)
class TranslatedMessage:
    """A canonical payload and the topic it is submitted under."""

    topic: str
    message: dict[str, Any]


@dataclass(slots=True)
class _Session:
    """The mod's own state, mirrored from what it does publish."""

    player_id: str | None = None
    player_position: dict[str, float] | None = None
    npc_positions: dict[str, dict[str, float]] = field(default_factory=dict)
    conversation: tuple[str, str] | None = None


class PrototypeWire:
    """Accept what the shipped mod publishes; emit what `docs/message_schemas.md` defines."""

    def __init__(self, defaults: PrototypeDefaults) -> None:
        self._defaults = defaults
        self._sessions: dict[str, _Session] = {}

    def translate(self, payload: object) -> TranslatedMessage:
        """Route one published payload, translating it only when it is prototype-shaped."""
        if not isinstance(payload, dict):
            raise PrototypeTranslationError("a published message must be a JSON object")

        if CANONICAL_TYPE in payload:
            return TranslatedMessage(_topic_for(payload[CANONICAL_TYPE]), payload)

        message_type = payload.get(PROTOTYPE_TYPE)
        if message_type is None:
            raise PrototypeTranslationError(
                f"a published message must carry {PROTOTYPE_TYPE!r} or {CANONICAL_TYPE!r}"
            )

        topic = _topic_for(message_type)
        session_id = _identifier(payload, "session_id")
        session = self._sessions.setdefault(session_id, _Session())
        translate = {
            TOPIC_WORLD_SNAPSHOT: self._world_snapshot,
            TOPIC_GAME_EVENT: self._game_event,
            TOPIC_CONVERSATION_TURN: self._conversation_turn,
        }[topic]
        return TranslatedMessage(topic, translate(payload, session_id, session))

    def _world_snapshot(
        self, payload: dict[str, Any], session_id: str, session: _Session
    ) -> dict[str, Any]:
        player = _object(payload, "player")
        observations = [_observation(entry) for entry in _array(payload, "npcs")]

        session.player_id = _identifier(player, "uuid")
        session.player_position = _position(player, "position")
        session.npc_positions = {
            observation["npc_id"]: observation["position"] for observation in observations
        }

        return {
            "schema_version": SCHEMA_VERSION,
            "message_type": "world_snapshot",
            "session_id": session_id,
            "world_id": self._defaults.world_id,
            "sequence": _whole(payload, "sequence"),
            "timestamp_ms": _whole(payload, "timestamp"),
            "candidate_policy": {
                "entry_radius_blocks": self._defaults.entry_radius_blocks,
                "exit_radius_blocks": self._defaults.exit_radius_blocks,
            },
            "player": {
                "player_id": session.player_id,
                "position": session.player_position,
                "look_direction": _look_direction(_object(player, "look")),
            },
            "active_conversation": _active_conversation(session, observations),
            "candidate_count": len(observations),
            "npcs": observations,
            "attention_edges": [],
        }

    def _game_event(
        self, payload: dict[str, Any], session_id: str, session: _Session
    ) -> dict[str, Any]:
        actor = _identifier(payload, "actor_uuid")
        target = _optional_identifier(payload, "target_uuid")
        event_id = _identifier(payload, "event_id")

        return {
            "schema_version": SCHEMA_VERSION,
            "message_type": "game_event",
            "session_id": session_id,
            "message_id": f"{MESSAGE_ID_PREFIX}-{event_id}-r{FIRST_EVENT_REVISION}",
            "event_id": event_id,
            "event_revision": FIRST_EVENT_REVISION,
            "timestamp_ms": _whole(payload, "timestamp"),
            "event_type": _identifier(payload, "event_type"),
            "status": EVENT_STATUS_STARTED,
            "position": _event_position(session, target, actor),
            "actor_npc_ids": [actor],
            "target_npc_ids": [target] if target is not None else [],
            "responder_npc_ids": [],
        }

    def _conversation_turn(
        self, payload: dict[str, Any], session_id: str, session: _Session
    ) -> dict[str, Any]:
        conversation_id = _identifier(payload, "conversation_id")
        target_npc_id = _identifier(payload, "npc_uuid")
        speaker_type = _identifier(payload, "speaker")

        session.conversation = (conversation_id, target_npc_id)

        return {
            "schema_version": SCHEMA_VERSION,
            "message_type": "conversation_turn",
            "session_id": session_id,
            "conversation_id": conversation_id,
            "turn_id": _identifier(payload, "turn_id"),
            "turn_index": _whole(payload, "turn_number"),
            "timestamp_ms": _whole(payload, "timestamp"),
            "speaker_type": speaker_type,
            "speaker_id": _speaker_id(session, payload, speaker_type),
            "target_npc_id": target_npc_id,
            "text": _text(payload, "message"),
        }


def _topic_for(message_type: object) -> str:
    topic = TOPIC_FOR_MESSAGE_TYPE.get(str(message_type))
    if topic is None:
        raise PrototypeTranslationError(f"unknown message type: {message_type!r}")
    return topic


def _active_conversation(
    session: _Session, observations: list[dict[str, Any]]
) -> dict[str, str] | None:
    """The conversation the mod last announced, while §1's target-is-a-candidate rule holds.

    The mod publishes no conversation end — `ConversationPublisher.endConversation` has no
    caller — so a conversation is only ever switched by talking to someone else. Leaving the
    candidate set therefore suspends the conversation rather than ending it, which is what the
    mod does too: walk back and it is still the one you were having.
    """
    if session.conversation is None:
        return None
    conversation_id, target_npc_id = session.conversation
    if target_npc_id not in {observation["npc_id"] for observation in observations}:
        return None
    return {"conversation_id": conversation_id, "target_npc_id": target_npc_id}


def _event_position(
    session: _Session, target: str | None, actor: str | None
) -> dict[str, float]:
    """Where the event happened, from where its participants were last seen.

    Event geometry decides witness membership, so a placeholder here would read as a valid
    event that nobody could have seen. The target is preferred because the mod's own event —
    `villager_attacked` — names the player as the actor and the villager as the target.
    """
    for participant in (target, actor):
        if participant is not None and participant in session.npc_positions:
            return dict(session.npc_positions[participant])
    if session.player_position is not None:
        return dict(session.player_position)
    raise PrototypeTranslationError(
        "no world snapshot has been received for this session, so the event position of an"
        " event between unobserved participants cannot be derived"
    )


def _speaker_id(session: _Session, payload: dict[str, Any], speaker_type: str) -> str:
    """§3 wants a stable identity; the mod sends a display name and, elsewhere, a UUID."""
    if speaker_type == SPEAKER_TYPE_PLAYER and session.player_id is not None:
        return session.player_id
    return _identifier(payload, "speaker_name")


def _look_direction(look: dict[str, Any]) -> dict[str, float]:
    """`ViewportMath.getLookDirection`, in the mod's own trigonometry and its own units."""
    yaw = math.radians(_number(look, "yaw"))
    pitch = math.radians(_number(look, "pitch"))
    x = -math.sin(yaw) * math.cos(pitch)
    y = -math.sin(pitch)
    z = math.cos(yaw) * math.cos(pitch)
    length = math.sqrt(x * x + y * y + z * z)
    if length == 0.0:
        # `Vec3.normalize` answers the zero vector rather than dividing by zero.
        return {"x": 0.0, "y": 0.0, "z": 0.0}
    return {"x": x / length, "y": y / length, "z": z / length}


def _observation(entry: object) -> dict[str, Any]:
    """One candidate, keeping only what §1 defines. The mod already publishes four of the six."""
    if not isinstance(entry, dict):
        raise PrototypeTranslationError("every entry of `npcs` must be an object")
    return {
        "npc_id": _identifier(entry, "uuid"),
        "position": _position(entry, "position"),
        "world_distance_blocks": _number(entry, "world_distance_blocks"),
        "viewport_center_distance": _number(entry, "viewport_center_distance"),
        "inside_viewport": _flag(entry, "inside_viewport"),
        "line_of_sight": _flag(entry, "line_of_sight"),
    }


def _position(payload: dict[str, Any], key: str) -> dict[str, float]:
    vector = _object(payload, key)
    return {axis: _number(vector, axis) for axis in ("x", "y", "z")}


def _object(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise PrototypeTranslationError(f"{key} must be an object")
    return value


def _array(payload: dict[str, Any], key: str) -> list[Any]:
    value = payload.get(key)
    if not isinstance(value, list):
        raise PrototypeTranslationError(f"{key} must be an array")
    return value


def _identifier(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise PrototypeTranslationError(f"{key} must be a non-empty string")
    return value


def _optional_identifier(payload: dict[str, Any], key: str) -> str | None:
    """The mod omits `target_uuid` and `details` rather than sending them null."""
    if payload.get(key) is None:
        return None
    return _identifier(payload, key)


def _text(payload: dict[str, Any], key: str) -> str:
    """§3 allows an empty utterance, so emptiness is not an error here."""
    value = payload.get(key)
    if not isinstance(value, str):
        raise PrototypeTranslationError(f"{key} must be a string")
    return value


def _whole(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise PrototypeTranslationError(f"{key} must be an integer")
    return value


def _number(payload: dict[str, Any], key: str) -> float:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PrototypeTranslationError(f"{key} must be a number")
    return float(value)


def _flag(payload: dict[str, Any], key: str) -> bool:
    value = payload.get(key)
    if not isinstance(value, bool):
        raise PrototypeTranslationError(f"{key} must be a boolean")
    return value
