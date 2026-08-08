"""Public input/output contracts used by the Attention Router.

Owner: Elson & Daniel

The backend owns the orchestration boundary in :mod:`backend.orchestration.router_port`.
Re-exporting those exact objects here gives Router code an owned import surface without
creating duplicate classes that would fail the backend handoff's runtime identity checks.
Scoring, assignment, graph, hysteresis, and state models belong in their respective Router
modules rather than this shared boundary facade.
"""

from backend.orchestration.router_port import (
    RESULT_SCHEMA_VERSION,
    RESULT_TYPE,
    SNAPSHOT_SCHEMA_VERSION,
    SNAPSHOT_TYPE,
    ActiveConversation,
    AttentionEdge,
    AttentionTier,
    CandidatePolicy,
    RouterConversationState,
    RouterInput,
    RouterPort,
    RoutingAssignment,
    RoutingDiagnostics,
    RoutingNpc,
    RoutingResult,
    RoutingSnapshot,
    TierCounts,
)

__all__ = [
    "RESULT_SCHEMA_VERSION",
    "RESULT_TYPE",
    "SNAPSHOT_SCHEMA_VERSION",
    "SNAPSHOT_TYPE",
    "ActiveConversation",
    "AttentionEdge",
    "AttentionTier",
    "CandidatePolicy",
    "RouterConversationState",
    "RouterInput",
    "RouterPort",
    "RoutingAssignment",
    "RoutingDiagnostics",
    "RoutingNpc",
    "RoutingResult",
    "RoutingSnapshot",
    "TierCounts",
]
