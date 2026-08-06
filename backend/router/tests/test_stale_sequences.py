"""Router tests: rejection of stale sequences.

Owner: Elson & Daniel
"""

from __future__ import annotations

import pytest

from backend.router.models import (
    SNAPSHOT_SCHEMA_VERSION,
    SNAPSHOT_TYPE,
    CandidatePolicy,
    RoutingSnapshot,
)
from backend.router.router import Router, StaleSnapshotError


def empty_snapshot(sequence: int) -> RoutingSnapshot:
    return RoutingSnapshot(
        schema_version=SNAPSHOT_SCHEMA_VERSION,
        snapshot_type=SNAPSHOT_TYPE,
        session_id="demo-01",
        world_id="minecraft-overworld-market",
        sequence=sequence,
        timestamp_ms=1_786_208_500_000 + sequence,
        candidate_policy=CandidatePolicy(
            entry_radius_blocks=24.0, exit_radius_blocks=28.0
        ),
        active_event_ids=[],
        active_conversation=None,
        candidate_count=0,
        npcs=[],
        attention_edges=[],
    )


def test_router_allows_same_sequence_refresh_but_rejects_older_state() -> None:
    router = Router()
    router.route(empty_snapshot(5))
    assert router.route(empty_snapshot(5)).sequence == 5

    with pytest.raises(StaleSnapshotError, match="older than accepted sequence 5"):
        router.route(empty_snapshot(4))

    router.reset_session("demo-01")
    assert router.route(empty_snapshot(4)).sequence == 4
