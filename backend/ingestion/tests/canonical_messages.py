"""Canonical world-snapshot payloads shared by the owned intake tests.

Owner: Jerome & Richard
"""

from __future__ import annotations

from typing import Any

from backend.ingestion.message_validation import SCHEMA_VERSION

SESSION_ID = "demo-01"
WORLD_ID = "overworld"
SHOPKEEPER = "shopkeeper-uuid"
THIEF = "thief-uuid"


def candidate(npc_id: str, **overrides: Any) -> dict[str, Any]:
    return {
        "npc_id": npc_id,
        "world_distance": 3.4,
        "viewport_center_distance": 0.07,
        "visible": True,
        "line_of_sight": True,
    } | overrides


def world_snapshot(sequence: int = 1, **overrides: Any) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "type": "world_snapshot",
        "session_id": SESSION_ID,
        "world_id": WORLD_ID,
        "sequence": sequence,
        "observed_at_ms": 1_786_208_500_123,
        "candidates": [candidate(SHOPKEEPER), candidate(THIEF, world_distance=11.2)],
        "active_conversation": None,
        "attention_edges": [],
    } | overrides
