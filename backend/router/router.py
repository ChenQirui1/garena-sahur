"""Public stateful Router entry point.

Owner: Elson & Daniel

Graph propagation remains intentionally excluded. The Router applies hysteresis to direct
scores before deterministic tier assignment.
"""

from __future__ import annotations

from backend.router.assignment import assign_tiers
from backend.router.config import RouterConfig
from backend.router.hysteresis import apply_hysteresis
from backend.router.models import (
    RESULT_SCHEMA_VERSION,
    RESULT_TYPE,
    RoutingResult,
    RoutingSnapshot,
)
from backend.router.scoring import score_snapshot
from backend.router.state import RouterState


class StaleSnapshotError(ValueError):
    """A snapshot is older than the latest accepted sequence for its session/world."""


class Router:
    """Persistent in-process Attention Router implementing the current shared port."""

    def __init__(self, config: RouterConfig | None = None) -> None:
        self.config = config or RouterConfig()
        self._state = RouterState()

    def route(self, snapshot: RoutingSnapshot) -> RoutingResult:
        """Score and assign one enriched snapshot, rejecting only older sequences."""
        last_sequence = self._state.last_sequence(
            snapshot.session_id, snapshot.world_id
        )
        if last_sequence is not None and snapshot.sequence < last_sequence:
            raise StaleSnapshotError(
                f"sequence {snapshot.sequence} is older than accepted sequence {last_sequence}"
            )

        scored = score_snapshot(snapshot, self.config)
        previous_states = self._state.npc_states(
            snapshot.session_id, snapshot.world_id
        )
        previous_tiers = {
            npc_id: state.tier for npc_id, state in previous_states.items()
        }
        ranking_scores: dict[str, float] = {}
        hysteresis_reasons: dict[str, str] = {}
        for candidate in scored:
            npc_id = candidate.npc.npc_id
            adjustment = apply_hysteresis(
                final_score=candidate.score.direct_score,
                previous=previous_states.get(npc_id),
                timestamp_ms=snapshot.timestamp_ms,
                config=self.config,
            )
            ranking_scores[npc_id] = adjustment.effective_score
            if adjustment.sticky_bonus > 0 and adjustment.reason is not None:
                hysteresis_reasons[npc_id] = adjustment.reason

        conversation = snapshot.active_conversation
        target = conversation.target_npc_id if conversation else None
        assignments = assign_tiers(
            candidates=scored,
            active_conversation_target=target,
            previous_tiers=previous_tiers,
            config=self.config,
            ranking_scores=ranking_scores,
            hysteresis_reasons=hysteresis_reasons,
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

        self._state.record(snapshot, assignments)
        return result

    def reset_session(self, session_id: str) -> None:
        """Remove all sequence and tier state for one session across its worlds."""
        self._state.reset_session(session_id)
