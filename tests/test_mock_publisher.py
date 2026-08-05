"""Contract smoke test for the Minecraft-side development publisher."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from backend.ingestion.message_validation import (
    validate_conversation_turn,
    validate_world_snapshot,
)

ROOT = Path(__file__).resolve().parents[1]
PUBLISHER = ROOT / "mock-publisher" / "publish.py"


def test_mock_publisher_emits_the_three_canonical_upstream_messages() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(PUBLISHER),
            "--npcs",
            "10",
            "--rate",
            "4",
            "--duration",
            "1",
            "--no-sleep",
            "--epoch-ms",
            "1786208500000",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    records = [json.loads(line) for line in completed.stdout.splitlines()]

    assert {record["topic"] for record in records} == {
        "world.snapshot",
        "game.event",
        "conversation.turn",
    }
    assert "npc.profile" not in completed.stdout

    snapshots = [
        validate_world_snapshot(record["message"])
        for record in records
        if record["topic"] == "world.snapshot"
    ]
    turns = [
        validate_conversation_turn(record["message"])
        for record in records
        if record["topic"] == "conversation.turn"
    ]
    event = next(record["message"] for record in records if record["topic"] == "game.event")

    assert snapshots[0].candidate_count == 10
    assert snapshots[-1].active_conversation is not None
    assert [turn.turn_id for turn in turns] == ["turn-004", "turn-005"]
    assert event == {
        "schema_version": "1.0",
        "message_type": "game_event",
        "session_id": "demo-01",
        "message_id": "event-message-001",
        "event_id": "market-theft-001",
        "event_revision": 1,
        "timestamp_ms": 1786208500250,
        "event_type": "market_theft",
        "status": "started",
        "position": {"x": 104.2, "y": 64.0, "z": -31.8},
        "actor_npc_ids": ["thief-uuid"],
        "target_npc_ids": ["shopkeeper-uuid"],
        "responder_npc_ids": ["guard-uuid"],
    }
