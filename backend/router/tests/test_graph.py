"""Router tests: one-hop attention propagation.

Owner: Elson & Daniel
"""

from __future__ import annotations

import pytest

from backend.router.config import RouterConfig
from backend.router.graph import PropagatedCandidate, propagate_attention
from backend.router.models import (
    SNAPSHOT_SCHEMA_VERSION,
    SNAPSHOT_TYPE,
    AttentionEdge,
    AttentionTier,
    CandidatePolicy,
    RoutingNpc,
    RoutingSnapshot,
)
from backend.router.router import Router
from backend.router.scoring import ScoreBreakdown, ScoredCandidate

EDGE_WEIGHTS = {"gaze": 1.0}
GRAPH_DECAY = 0.5


def candidate(npc_id: str, direct_score: float) -> ScoredCandidate:
    npc = RoutingNpc(
        npc_id=npc_id,
        world_distance_blocks=5.0,
        viewport_center_distance=1.0,
        inside_viewport=False,
        line_of_sight=False,
        event_relevance=0.0,
        event_roles=[],
        interaction_recency=0.0,
    )
    score = ScoreBreakdown(
        viewport=0.0,
        proximity=0.0,
        event_relevance=0.0,
        interaction_recency=0.0,
        active_conversation=False,
        direct_score=direct_score,
        reasons=(),
    )
    return ScoredCandidate(npc=npc, score=score)


def edge(
    source: str,
    target: str,
    *,
    kind: str = "gaze",
    active: bool = True,
) -> AttentionEdge:
    return AttentionEdge(
        source_npc_id=source,
        target_npc_id=target,
        kind=kind,
        active=active,
    )


def by_id(
    candidates: tuple[ScoredCandidate, ...],
    edges: list[AttentionEdge],
) -> dict[str, PropagatedCandidate]:
    propagated = propagate_attention(
        candidates,
        edges,
        edge_weights=EDGE_WEIGHTS,
        graph_decay=GRAPH_DECAY,
    )
    return {one.candidate.npc.npc_id: one for one in propagated}


def test_attention_propagates_only_in_the_edge_direction() -> None:
    result = by_id(
        (candidate("a", 0.8), candidate("b", 0.1)),
        [edge("a", "b")],
    )

    assert result["a"].propagated_score == 0.0
    assert result["b"].propagated_score == pytest.approx(0.4)
    assert result["b"].final_score == pytest.approx(0.4)
    assert result["b"].propagation_source_npc_id == "a"
    assert result["b"].reason == "one-hop attention from a"


def test_inactive_edge_contributes_nothing() -> None:
    result = by_id(
        (candidate("a", 0.8), candidate("b", 0.1)),
        [edge("a", "b", active=False)],
    )

    assert result["b"].propagated_score == 0.0
    assert result["b"].final_score == pytest.approx(0.1)
    assert result["b"].propagation_source_npc_id is None
    assert result["b"].reason is None


def test_target_uses_strongest_incoming_edge_instead_of_sum() -> None:
    result = by_id(
        (
            candidate("a", 0.8),
            candidate("b", 0.6),
            candidate("c", 0.0),
        ),
        [edge("a", "c"), edge("b", "c")],
    )

    assert result["c"].propagated_score == pytest.approx(0.4)
    assert result["c"].propagation_source_npc_id == "a"


def test_propagation_does_not_continue_across_multiple_hops() -> None:
    result = by_id(
        (
            candidate("a", 0.8),
            candidate("b", 0.1),
            candidate("c", 0.0),
        ),
        [edge("a", "b"), edge("b", "c")],
    )

    assert result["b"].propagated_score == pytest.approx(0.4)
    assert result["c"].propagated_score == pytest.approx(0.05)
    assert result["c"].propagation_source_npc_id == "b"


def test_source_direct_score_is_capped_at_one() -> None:
    result = by_id(
        (candidate("conversation-target", 10.8), candidate("b", 0.0)),
        [edge("conversation-target", "b")],
    )

    assert result["b"].propagated_score == pytest.approx(0.5)


def test_unknown_edge_kind_is_ignored_without_error() -> None:
    result = by_id(
        (candidate("a", 0.8), candidate("b", 0.1)),
        [edge("a", "b", kind="unsupported")],
    )

    assert result["b"].propagated_score == 0.0
    assert result["b"].final_score == pytest.approx(0.1)


def test_self_edge_is_ignored() -> None:
    result = by_id((candidate("a", 0.2),), [edge("a", "a")])

    assert result["a"].propagated_score == 0.0
    assert result["a"].final_score == pytest.approx(0.2)


def test_candidate_order_is_preserved_regardless_of_edge_order() -> None:
    candidates = (
        candidate("c", 0.0),
        candidate("a", 0.8),
        candidate("b", 0.6),
    )
    propagated = propagate_attention(
        candidates,
        [edge("b", "c"), edge("a", "c")],
        edge_weights=EDGE_WEIGHTS,
        graph_decay=GRAPH_DECAY,
    )

    assert [one.candidate.npc.npc_id for one in propagated] == ["c", "a", "b"]


def test_equal_propagation_uses_stable_source_npc_id() -> None:
    candidates = (
        candidate("source-z", 0.8),
        candidate("source-a", 0.8),
        candidate("target", 0.0),
    )
    forward = by_id(
        candidates,
        [edge("source-z", "target"), edge("source-a", "target")],
    )
    reversed_edges = by_id(
        candidates,
        [edge("source-a", "target"), edge("source-z", "target")],
    )

    assert forward["target"].propagation_source_npc_id == "source-a"
    assert reversed_edges["target"].propagation_source_npc_id == "source-a"


def test_stronger_direct_score_remains_final_and_graph_score_stays_visible() -> None:
    result = by_id(
        (candidate("a", 0.8), candidate("b", 0.7)),
        [edge("a", "b")],
    )

    assert result["b"].propagated_score == pytest.approx(0.4)
    assert result["b"].final_score == pytest.approx(0.7)
    assert result["b"].propagation_source_npc_id == "a"
    assert result["b"].reason is None


def test_edge_with_unknown_endpoint_is_ignored() -> None:
    result = by_id(
        (candidate("a", 0.8), candidate("b", 0.1)),
        [edge("missing", "b"), edge("a", "missing")],
    )

    assert result["a"].propagated_score == 0.0
    assert result["b"].propagated_score == 0.0


def test_non_positive_edge_weight_is_ignored() -> None:
    propagated = propagate_attention(
        (candidate("a", 0.8), candidate("b", 0.1)),
        [edge("a", "b")],
        edge_weights={"gaze": 0.0},
        graph_decay=GRAPH_DECAY,
    )

    result = {one.candidate.npc.npc_id: one for one in propagated}
    assert result["b"].propagated_score == 0.0
    assert result["b"].final_score == pytest.approx(0.1)


def test_graph_configuration_is_validated_and_not_accidentally_mutable() -> None:
    with pytest.raises(ValueError, match="graph_decay"):
        RouterConfig(graph_decay=-0.1)
    with pytest.raises(ValueError, match="edge_weights"):
        RouterConfig(edge_weights={"gaze": -0.1})

    supplied = {"gaze": 1.0}
    config = RouterConfig(edge_weights=supplied)
    supplied["gaze"] = 0.0

    assert config.edge_weights == {"gaze": 1.0}
    with pytest.raises(TypeError):
        config.edge_weights["gaze"] = 0.0  # type: ignore[index]


@pytest.mark.parametrize("non_finite", [float("nan"), float("inf"), float("-inf")])
def test_graph_configuration_rejects_non_finite_values(non_finite: float) -> None:
    with pytest.raises(ValueError, match="graph_decay must be finite"):
        RouterConfig(graph_decay=non_finite)
    with pytest.raises(ValueError, match="edge_weights.*must be finite"):
        RouterConfig(edge_weights={"gaze": non_finite})


def test_router_ranks_by_graph_final_score_and_reports_score_evidence() -> None:
    config = RouterConfig(
        focused_capacity=2,
        reactive_capacity=0,
        viewport_weight=0.0,
        proximity_weight=0.0,
        event_relevance_weight=1.0,
        interaction_recency_weight=0.0,
        graph_decay=0.5,
    )
    npcs = [
        RoutingNpc(
            npc_id="source",
            world_distance_blocks=5.0,
            viewport_center_distance=1.0,
            inside_viewport=False,
            line_of_sight=False,
            event_relevance=1.0,
            event_roles=["actor"],
            interaction_recency=0.0,
        ),
        RoutingNpc(
            npc_id="graph-target",
            world_distance_blocks=5.0,
            viewport_center_distance=1.0,
            inside_viewport=False,
            line_of_sight=False,
            event_relevance=0.1,
            event_roles=[],
            interaction_recency=0.0,
        ),
        RoutingNpc(
            npc_id="direct-competitor",
            world_distance_blocks=5.0,
            viewport_center_distance=1.0,
            inside_viewport=False,
            line_of_sight=False,
            event_relevance=0.4,
            event_roles=[],
            interaction_recency=0.0,
        ),
    ]
    snapshot = RoutingSnapshot(
        schema_version=SNAPSHOT_SCHEMA_VERSION,
        snapshot_type=SNAPSHOT_TYPE,
        session_id="graph-integration",
        world_id="market",
        sequence=1,
        timestamp_ms=10_000,
        candidate_policy=CandidatePolicy(
            entry_radius_blocks=24.0,
            exit_radius_blocks=28.0,
        ),
        active_event_ids=["event-1"],
        active_conversation=None,
        candidate_count=len(npcs),
        npcs=npcs,
        attention_edges=[edge("source", "graph-target")],
    )

    result = Router(config).route(snapshot)
    assignments = {one.npc_id: one for one in result.assignments}

    assert assignments["source"].tier is AttentionTier.FOCUSED
    assert assignments["graph-target"].tier is AttentionTier.FOCUSED
    assert assignments["direct-competitor"].tier is AttentionTier.AMBIENT
    assert assignments["graph-target"].direct_score == pytest.approx(0.1)
    assert assignments["graph-target"].propagated_score == pytest.approx(0.5)
    assert assignments["graph-target"].final_score == pytest.approx(0.5)
    assert "one-hop attention from source" in assignments["graph-target"].reasons
    assert result.counts is not None
    assert result.counts.focused == 2
    assert result.counts.reactive == 0
    assert result.counts.ambient == 1
    assert result.diagnostics is not None
    assert result.diagnostics.focused_capacity == 2
    assert result.diagnostics.reactive_capacity == 0
    assert result.diagnostics.candidate_count == 3
    assert result.diagnostics.routing_time_ms >= 0.0
