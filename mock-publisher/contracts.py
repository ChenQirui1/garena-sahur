"""Canonical schema-version-1.0 messages emitted by the mock Minecraft publisher.

The mock publisher produces the three upstream topics defined in
``docs/message_schemas.md``. Static NPC profiles are backend-owned data and are not
published by Minecraft.
"""

from __future__ import annotations

SCHEMA_VERSION = "1.0"

TOPIC_SNAPSHOT = "world.snapshot"
TOPIC_EVENT = "game.event"
TOPIC_TURN = "conversation.turn"

UPSTREAM_TOPICS = (TOPIC_SNAPSHOT, TOPIC_EVENT, TOPIC_TURN)


def vector3(x, y, z):
    return {"x": float(x), "y": float(y), "z": float(z)}


def npc_observation(
    npc_id,
    position,
    world_distance_blocks,
    viewport_center_distance,
    inside_viewport,
    line_of_sight,
):
    """One raw, radius-selected Minecraft NPC observation."""
    return {
        "npc_id": npc_id,
        "position": position,
        "world_distance_blocks": round(float(world_distance_blocks), 3),
        "viewport_center_distance": round(float(viewport_center_distance), 4),
        "inside_viewport": bool(inside_viewport),
        "line_of_sight": bool(line_of_sight),
    }


def world_snapshot(
    session_id,
    world_id,
    sequence,
    timestamp_ms,
    player,
    active_conversation,
    npcs,
    attention_edges=None,
    entry_radius_blocks=24.0,
    exit_radius_blocks=28.0,
):
    """One batched, latest-value-wins world snapshot."""
    return {
        "schema_version": SCHEMA_VERSION,
        "message_type": "world_snapshot",
        "session_id": session_id,
        "world_id": world_id,
        "sequence": int(sequence),
        "timestamp_ms": int(timestamp_ms),
        "candidate_policy": {
            "entry_radius_blocks": float(entry_radius_blocks),
            "exit_radius_blocks": float(exit_radius_blocks),
        },
        "player": player,
        "active_conversation": active_conversation,
        "candidate_count": len(npcs),
        "npcs": list(npcs),
        "attention_edges": list(attention_edges or []),
    }


def game_event(
    session_id,
    message_id,
    event_id,
    event_revision,
    timestamp_ms,
    event_type,
    status,
    position,
    actor_npc_ids=None,
    target_npc_ids=None,
    responder_npc_ids=None,
):
    """One complete revision of a durable game event."""
    return {
        "schema_version": SCHEMA_VERSION,
        "message_type": "game_event",
        "session_id": session_id,
        "message_id": message_id,
        "event_id": event_id,
        "event_revision": int(event_revision),
        "timestamp_ms": int(timestamp_ms),
        "event_type": event_type,
        "status": status,
        "position": position,
        "actor_npc_ids": list(actor_npc_ids or []),
        "target_npc_ids": list(target_npc_ids or []),
        "responder_npc_ids": list(responder_npc_ids or []),
    }


def conversation_turn(
    session_id,
    conversation_id,
    turn_id,
    turn_index,
    timestamp_ms,
    speaker_type,
    speaker_id,
    target_npc_id,
    text,
):
    """One durable conversation turn, deduplicated by ``turn_id``."""
    return {
        "schema_version": SCHEMA_VERSION,
        "message_type": "conversation_turn",
        "session_id": session_id,
        "conversation_id": conversation_id,
        "turn_id": turn_id,
        "turn_index": int(turn_index),
        "timestamp_ms": int(timestamp_ms),
        "speaker_type": speaker_type,
        "speaker_id": speaker_id,
        "target_npc_id": target_npc_id,
        "text": text,
    }


_REQUIRED = {
    "world_snapshot": {
        "schema_version",
        "message_type",
        "session_id",
        "world_id",
        "sequence",
        "timestamp_ms",
        "candidate_policy",
        "player",
        "active_conversation",
        "candidate_count",
        "npcs",
        "attention_edges",
    },
    "game_event": {
        "schema_version",
        "message_type",
        "session_id",
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
    },
    "conversation_turn": {
        "schema_version",
        "message_type",
        "session_id",
        "conversation_id",
        "turn_id",
        "turn_index",
        "timestamp_ms",
        "speaker_type",
        "speaker_id",
        "target_npc_id",
        "text",
    },
}


def validate(message):
    """Reject a malformed mock message before a sink publishes it."""
    kind = message.get("message_type")
    required = _REQUIRED.get(kind)
    if required is None:
        raise ValueError(f"unknown message_type: {kind!r}")
    if message.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"unsupported schema_version: {message.get('schema_version')!r}")
    missing = required - message.keys()
    if missing:
        raise ValueError(f"{kind} missing fields: {sorted(missing)}")
    unknown = message.keys() - required
    if unknown:
        raise ValueError(f"{kind} has unknown fields: {sorted(unknown)}")

    if kind == "world_snapshot":
        _validate_world_snapshot(message)
    return message


def _validate_world_snapshot(message):
    npcs = message["npcs"]
    if message["candidate_count"] != len(npcs):
        raise ValueError("candidate_count must equal len(npcs)")
    npc_ids = [npc["npc_id"] for npc in npcs]
    if len(npc_ids) != len(set(npc_ids)):
        raise ValueError("npcs must have unique npc_id values")

    conversation = message["active_conversation"]
    if conversation and conversation["target_npc_id"] not in npc_ids:
        raise ValueError("active conversation target must be a candidate")
    for edge in message["attention_edges"]:
        if edge["source_npc_id"] not in npc_ids or edge["target_npc_id"] not in npc_ids:
            raise ValueError("attention edge endpoints must be candidates")
