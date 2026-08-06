"""Public direct-scoring Router entry point.

Owner: Elson & Daniel

This first working Router intentionally excludes graph propagation and hysteresis. It provides
the deterministic baseline those later modules will extend: direct scoring, hard capacities,
conversation priority, previous-tier tie-breaking, and stale-sequence protection.
"""

from __future__ import annotations

from backend.router.assignment import assign_tiers
from backend.router.config import RouterConfig
from backend.router.models import (
    RESULT_SCHEMA_VERSION,
    RESULT_TYPE,
    AttentionTier,
    RoutingResult,
    RoutingSnapshot,
)
from backend.router.scoring import score_snapshot


class StaleSnapshotError(ValueError):
    """A snapshot is older than the latest accepted sequence for its session/world."""


class Router:
    """Persistent in-process Attention Router implementing the current shared port."""

    def __init__(self, config: RouterConfig | None = None) -> None:
        self.config = config or RouterConfig()
        self._last_sequences: dict[tuple[str, str], int] = {}
        self._previous_tiers: dict[tuple[str, str], dict[str, AttentionTier]] = {}

    def route(self, snapshot: RoutingSnapshot) -> RoutingResult:
        """Score and assign one enriched snapshot, rejecting only older sequences."""
        key = (snapshot.session_id, snapshot.world_id)
        last_sequence = self._last_sequences.get(key)
        if last_sequence is not None and snapshot.sequence < last_sequence:
            raise StaleSnapshotError(
                f"sequence {snapshot.sequence} is older than accepted sequence {last_sequence}"
            )

        scored = score_snapshot(snapshot, self.config)
        conversation = snapshot.active_conversation
        target = conversation.target_npc_id if conversation else None
        assignments = assign_tiers(
            candidates=scored,
            active_conversation_target=target,
            previous_tiers=self._previous_tiers.get(key, {}),
            config=self.config,
        )

        result = RoutingResult(
            schema_version=RESULT_SCHEMA_VERSION,
            result_type=RESULT_TYPE,
            session_id=snapshot.session_id,
            world_id=snapshot.world_id,
            sequence=snapshot.sequence,
            timestamp_ms=snapshot.timestamp_ms,
            assignments=assignments,
        )

        self._last_sequences[key] = snapshot.sequence
        self._previous_tiers[key] = {
            assignment.npc_id: assignment.tier for assignment in assignments
        }
        return result

    def reset_session(self, session_id: str) -> None:
        """Remove all sequence and tier state for one session across its worlds."""
        keys = [key for key in self._last_sequences if key[0] == session_id]
        for key in keys:
            self._last_sequences.pop(key, None)
            self._previous_tiers.pop(key, None)
