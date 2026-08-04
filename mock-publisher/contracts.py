"""Shared message contracts for the mock publisher.

These builders produce messages that match the agreed structures in
``docs/team-architecture.md`` §8 — Shared Message Contracts. The mock
publisher stands in for Ivan's Minecraft / pub-sub client side, so the
downstream Python backend (ingestion -> router -> orchestration) can be
run and tested without a live game.

Only the four *upstream* topics are produced here — the ones the backend
subscribes to:

    npc.profile         (startup / profile change)
    world.snapshot      (high frequency, latest-value-wins)
    game.event          (durable, dedup by event_id)
    conversation.turn   (durable, dedup by turn_id)

The downstream (routing.assignment / behaviour.command / telemetry.record)
is intentionally *not* produced here — that is what consumes this feed.
"""

from __future__ import annotations

# Transport topic names, kept identical to the architecture diagram.
TOPIC_PROFILE = "npc.profile"
TOPIC_SNAPSHOT = "world.snapshot"
TOPIC_EVENT = "game.event"
TOPIC_TURN = "conversation.turn"

UPSTREAM_TOPICS = (TOPIC_PROFILE, TOPIC_SNAPSHOT, TOPIC_EVENT, TOPIC_TURN)


def npc_profile(npc_id, name, role, persona, relationships=None):
    """npc.profile — persona, role and authored relationships.

    §8 lists this topic but does not pin a full field-level schema; this is
    the proposed shape (see mock-publisher/README.md). Published once per NPC
    on startup.
    """
    return {
        "type": "npc_profile",
        "npc_id": npc_id,
        "name": name,
        "role": role,
        "persona": persona,
        "relationships": relationships or {},
    }


def world_snapshot(session_id, sequence, timestamp_ms, npcs):
    """world.snapshot — high-frequency, latest-value-wins.

    ``npcs`` is a list of dicts already shaped by :func:`npc_observation`.
    """
    return {
        "type": "world_snapshot",
        "session_id": session_id,
        "sequence": sequence,
        "timestamp_ms": timestamp_ms,
        "npcs": npcs,
    }


def npc_observation(
    npc_id,
    world_distance,
    viewport_center_distance,
    visible,
    line_of_sight,
    event_relevance,
    interaction_recency,
    active_conversation,
):
    """A single NPC entry inside a world snapshot."""
    return {
        "npc_id": npc_id,
        "world_distance": round(float(world_distance), 3),
        "viewport_center_distance": round(float(viewport_center_distance), 4),
        "visible": bool(visible),
        "line_of_sight": bool(line_of_sight),
        "event_relevance": round(float(event_relevance), 3),
        "interaction_recency": round(float(interaction_recency), 3),
        "active_conversation": bool(active_conversation),
    }


def game_event(event_id, event_type, summary, participants):
    """game.event — durable, deduplicated by event_id."""
    return {
        "type": "game_event",
        "event_id": event_id,
        "event_type": event_type,
        "summary": summary,
        "participants": list(participants),
    }


def conversation_turn(conversation_id, turn_id, npc_id, player_text):
    """conversation.turn — durable, deduplicated by turn_id."""
    return {
        "type": "conversation_turn",
        "conversation_id": conversation_id,
        "turn_id": turn_id,
        "npc_id": npc_id,
        "player_text": player_text,
    }


# --- lightweight self-check -------------------------------------------------

_REQUIRED = {
    "npc_profile": {"type", "npc_id", "name", "role", "persona", "relationships"},
    "world_snapshot": {"type", "session_id", "sequence", "timestamp_ms", "npcs"},
    "game_event": {"type", "event_id", "event_type", "summary", "participants"},
    "conversation_turn": {"type", "conversation_id", "turn_id", "npc_id", "player_text"},
}


def validate(message):
    """Assert a message carries every required field for its ``type``.

    Cheap guard so the publisher never emits a malformed contract; raises
    ``ValueError`` on the first missing field.
    """
    kind = message.get("type")
    required = _REQUIRED.get(kind)
    if required is None:
        raise ValueError(f"unknown message type: {kind!r}")
    missing = required - message.keys()
    if missing:
        raise ValueError(f"{kind} missing fields: {sorted(missing)}")
    return message
