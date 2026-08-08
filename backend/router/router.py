"""Public stateful Router entry point.

Owner: Elson & Daniel

The Router calculates direct scores, applies exactly one graph hop, then lets hysteresis affect
ranking before the existing hard-capacity assignment. Reported scores stay pre-hysteresis so
telemetry describes the current snapshot rather than hidden state.
"""

from __future__ import annotations

from time import perf_counter

from backend.router.assignment import assign_tiers
from backend.router.config import RouterConfig
from backend.router.graph import propagate_attention
from backend.router.hysteresis import apply_hysteresis
from backend.router.models import (
    RESULT_SCHEMA_VERSION,
    RESULT_TYPE,
    AttentionTier,
    RoutingDiagnostics,
    RoutingResult,
    RoutingSnapshot,
    TierCounts,
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
        """Route one snapshot with one-hop propagation and capacity-safe hysteresis."""
        started_at = perf_counter()
        last_sequence = self._state.last_sequence(
            snapshot.session_id, snapshot.world_id
        )
        if last_sequence is not None and snapshot.sequence < last_sequence:
            raise StaleSnapshotError(
                f"sequence {snapshot.sequence} is older than accepted sequence {last_sequence}"
            )

        scored = score_snapshot(snapshot, self.config)
        propagated = propagate_attention(
            candidates=scored,
            edges=snapshot.attention_edges,
            edge_weights=self.config.edge_weights,
            graph_decay=self.config.graph_decay,
        )
        previous_states = self._state.npc_states(
            snapshot.session_id, snapshot.world_id
        )
        previous_tiers = {
            npc_id: state.tier for npc_id, state in previous_states.items()
        }
        ranking_scores: dict[str, float] = {}
        hysteresis_reasons: dict[str, str] = {}
        propagated_scores: dict[str, float] = {}
        final_scores: dict[str, float] = {}
        propagation_reasons: dict[str, str] = {}

        for propagated_candidate in propagated:
            candidate = propagated_candidate.candidate
            npc_id = candidate.npc.npc_id
            propagated_scores[npc_id] = propagated_candidate.propagated_score
            final_scores[npc_id] = propagated_candidate.final_score
            if propagated_candidate.reason is not None:
                propagation_reasons[npc_id] = propagated_candidate.reason

            adjustment = apply_hysteresis(
                final_score=propagated_candidate.final_score,
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
            propagated_scores=propagated_scores,
            final_scores=final_scores,
            propagation_reasons=propagation_reasons,
        )

        counts = TierCounts(
            focused=sum(
                assignment.tier is AttentionTier.FOCUSED
                for assignment in assignments
            ),
            reactive=sum(
                assignment.tier is AttentionTier.REACTIVE
                for assignment in assignments
            ),
            ambient=sum(
                assignment.tier is AttentionTier.AMBIENT
                for assignment in assignments
            ),
        )
        diagnostics = RoutingDiagnostics(
            focused_capacity=self.config.focused_capacity,
            reactive_capacity=self.config.reactive_capacity,
            candidate_count=snapshot.candidate_count,
            routing_time_ms=(perf_counter() - started_at) * 1000.0,
        )
        result = RoutingResult(
            schema_version=RESULT_SCHEMA_VERSION,
            result_type=RESULT_TYPE,
            session_id=snapshot.session_id,
            world_id=snapshot.world_id,
            sequence=snapshot.sequence,
            timestamp_ms=snapshot.timestamp_ms,
            assignments=assignments,
            counts=counts,
            diagnostics=diagnostics,
        )

        self._state.record(snapshot, assignments)
        return result

    def reset_session(self, session_id: str) -> None:
        """Remove all sequence and tier state for one session across its worlds."""
        self._state.reset_session(session_id)
