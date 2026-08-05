"""The team-sent world-snapshot payload the owned intake tests are written against.

Owner: Jerome & Richard

The literal below is the representative `world.snapshot` the team circulated on 2026-08-05,
so a rename or a dropped field fails the suite rather than reaching Ivan's publisher.
"""

from __future__ import annotations

from typing import Any

from backend.ingestion.message_validation import SCHEMA_VERSION

SESSION_ID = "demo-01"
WORLD_ID = "minecraft-overworld-market"
SHOPKEEPER = "shopkeeper-uuid"
THIEF = "thief-uuid"
CONVERSATION_ID = "conversation-07"
TIMESTAMP_MS = 1_786_208_500_123


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
