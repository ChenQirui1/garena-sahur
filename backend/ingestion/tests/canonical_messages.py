"""The team-sent payloads the owned intake tests are written against.

Owner: Jerome & Richard

The literals below are the representative `world.snapshot`, `game.event`, and
`conversation.turn` the team circulated on 2026-08-05, so a rename or a dropped field fails the
suite rather than reaching Ivan's publisher.
"""

from __future__ import annotations

from typing import Any

from backend.ingestion.message_validation import SCHEMA_VERSION

SESSION_ID = "demo-01"
WORLD_ID = "minecraft-overworld-market"
SHOPKEEPER = "shopkeeper-uuid"
THIEF = "thief-uuid"
GUARD = "guard-uuid"
CONVERSATION_ID = "conversation-07"
TURN_ID = "turn-004"
PLAYER_ID = "player-uuid"
TIMESTAMP_MS = 1_786_208_500_123
TURN_TIMESTAMP_MS = 1_786_208_500_200
EVENT_ID = "market-theft-001"
EVENT_MESSAGE_ID = "event-message-001"
EVENT_TIMESTAMP_MS = 1_786_208_495_000

# The event happens at the stall. The default NPC position is ~4.2 blocks from it, so a
# candidate placed with `npc()` is inside the witness radius unless a test moves it.
EVENT_POSITION = {"x": 104.2, "y": 64.0, "z": -31.8}
WITHIN_WITNESS_RADIUS = {"x": 108.1, "y": 64.0, "z": -30.2}
WITHIN_NEARBY_BAND = {"x": 120.0, "y": 64.0, "z": -31.8}
BEYOND_NEARBY_BAND = {"x": 140.0, "y": 64.0, "z": -31.8}


def npc(npc_id: str, **overrides: Any) -> dict[str, Any]:
    return {
        "npc_id": npc_id,
        "position": {"x": 108.1, "y": 64.0, "z": -30.2},
        "world_distance_blocks": 3.4,
        "viewport_center_distance": 0.07,
        "inside_viewport": True,
        "line_of_sight": True,
    } | overrides


def world_snapshot(sequence: int = 1842, **overrides: Any) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "message_type": "world_snapshot",
        "session_id": SESSION_ID,
        "world_id": WORLD_ID,
        "sequence": sequence,
        "timestamp_ms": TIMESTAMP_MS,
        "candidate_policy": {"entry_radius_blocks": 24.0, "exit_radius_blocks": 28.0},
        "player": {
            "player_id": "player-uuid",
            "position": {"x": 105.2, "y": 64.0, "z": -31.8},
            "look_direction": {"x": 0.72, "y": -0.05, "z": 0.69},
        },
        "active_conversation": None,
        "candidate_count": 2,
        "npcs": [npc(SHOPKEEPER), npc(THIEF, world_distance_blocks=11.2)],
        "attention_edges": [],
    } | overrides


def conversation_turn(**overrides: Any) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "message_type": "conversation_turn",
        "session_id": SESSION_ID,
        "conversation_id": CONVERSATION_ID,
        "turn_id": TURN_ID,
        "turn_index": 4,
        "timestamp_ms": TURN_TIMESTAMP_MS,
        "speaker_type": "player",
        "speaker_id": PLAYER_ID,
        "target_npc_id": SHOPKEEPER,
        "text": "Which direction did the thief run?",
    } | overrides


def game_event(revision: int = 1, **overrides: Any) -> dict[str, Any]:
    """Revision 1 is the §11.2 payload verbatim; later revisions vary only their delivery."""
    return {
        "schema_version": SCHEMA_VERSION,
        "message_type": "game_event",
        "session_id": SESSION_ID,
        "message_id": (
            EVENT_MESSAGE_ID if revision == 1 else f"{EVENT_MESSAGE_ID}-r{revision}"
        ),
        "event_id": EVENT_ID,
        "event_revision": revision,
        "timestamp_ms": EVENT_TIMESTAMP_MS + (revision - 1),
        "event_type": "market_theft",
        "status": "started",
        "position": dict(EVENT_POSITION),
        "actor_npc_ids": [THIEF],
        "target_npc_ids": [SHOPKEEPER],
        "responder_npc_ids": [GUARD],
    } | overrides


def active_conversation(target_npc_id: str = SHOPKEEPER) -> dict[str, str]:
    return {"conversation_id": CONVERSATION_ID, "target_npc_id": target_npc_id}


def attention_edge(
    source: str = THIEF, target: str = SHOPKEEPER, **overrides: Any
) -> dict[str, Any]:
    return {
        "source_npc_id": source,
        "target_npc_id": target,
        "kind": "gaze",
        "active": True,
    } | overrides
