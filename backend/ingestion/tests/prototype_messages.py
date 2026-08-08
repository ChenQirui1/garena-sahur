"""The payloads the shipped Fabric mod actually publishes.

Owner: Jerome & Richard

Every literal here is taken from `minecraft-fabric-mod/` at `1333971` — `SnapshotBuilder`,
`GameEventPublisher`, `ConversationPublisher`, and the `sequence`/`session_id` pair
`HttpPublisher.send` adds on the way out — and from Ivan's wire contract on issue #2. They are
prototype shapes on purpose: a canonical fixture here would prove the adapter agrees with our
own contract rather than with the publisher it exists to accept.
"""

from __future__ import annotations

from typing import Any

from backend.ingestion.tests.canonical_messages import SHOPKEEPER, THIEF

# `SpotlightConfig.SESSION_ID`, a compile-time constant in the shipped mod.
SESSION_ID = "minecraft-spotlight-001"

PLAYER_ID = "11111111-1111-1111-1111-111111111111"
PLAYER_NAME = "Jerome"

SNAPSHOT_TIMESTAMP_MS = 1_786_208_500_123
EVENT_TIMESTAMP_MS = 1_786_208_495_000
TURN_TIMESTAMP_MS = 1_786_208_500_200

EVENT_ID = "77777777-7777-7777-7777-777777777777"
CONVERSATION_ID = "88888888-8888-8888-8888-888888888888"
TURN_ID = "99999999-9999-9999-9999-999999999999"

SHOPKEEPER_POSITION = {"x": 108.1, "y": 64.0, "z": -30.2}
THIEF_POSITION = {"x": 112.4, "y": 64.0, "z": -35.1}
PLAYER_POSITION = {"x": 105.2, "y": 64.0, "z": -31.8}

# `player.getYRot()` and `player.getXRot()` in degrees, which `ViewportMath.getLookDirection`
# turns into the unit vector §1 asks for.
LOOK = {"yaw": -45.0, "pitch": 10.0}


def npc(npc_id: str = SHOPKEEPER, **overrides: Any) -> dict[str, Any]:
    """One entry of `SnapshotBuilder.buildNpc`, prototype extras included."""
    return {
        "uuid": npc_id,
        "name": "Mira",
        "profession": "Farmer",
        "level": 1,
        "position": dict(SHOPKEEPER_POSITION),
        "distance": 3.4,
        "world_distance_blocks": 3.4,
        "viewport_center_distance": 0.07,
        "inside_viewport": True,
        "line_of_sight": True,
        "health": 20.0,
        "max_health": 20.0,
        "activity": "idle",
    } | overrides


def world_snapshot(sequence: int = 1842, **overrides: Any) -> dict[str, Any]:
    return {
        "type": "world_snapshot",
        "timestamp": SNAPSHOT_TIMESTAMP_MS,
        "player": {
            "uuid": PLAYER_ID,
            "name": PLAYER_NAME,
            "position": dict(PLAYER_POSITION),
            "look": dict(LOOK),
            "held_item": "minecraft:bread",
        },
        "npcs": [
            npc(SHOPKEEPER),
            npc(THIEF, position=dict(THIEF_POSITION), world_distance_blocks=11.2),
        ],
        "sequence": sequence,
        "session_id": SESSION_ID,
    } | overrides


def game_event(sequence: int = 43, **overrides: Any) -> dict[str, Any]:
    """`villager_attacked`: the actor is the *player*, the target is the villager."""
    return {
        "type": "game_event",
        "timestamp": EVENT_TIMESTAMP_MS,
        "event_id": EVENT_ID,
        "event_type": "villager_attacked",
        "actor_uuid": PLAYER_ID,
        "target_uuid": SHOPKEEPER,
        "details": "Player attacked a villager",
        "sequence": sequence,
        "session_id": SESSION_ID,
    } | overrides


def conversation_turn(sequence: int = 44, **overrides: Any) -> dict[str, Any]:
    return {
        "type": "conversation_turn",
        "timestamp": TURN_TIMESTAMP_MS,
        "npc_uuid": SHOPKEEPER,
        "conversation_id": CONVERSATION_ID,
        "turn_id": TURN_ID,
        "speaker": "player",
        "speaker_name": PLAYER_NAME,
        "message": "Which direction did the thief run?",
        "turn_number": 1,
        "conversation_start": TURN_TIMESTAMP_MS,
        "sequence": sequence,
        "session_id": SESSION_ID,
    } | overrides
