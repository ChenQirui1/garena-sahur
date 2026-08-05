"""Contract fakes standing in for Elson & Daniel's Router at the owned port.

Owner: Jerome & Richard

Passing against these proves the owned handoff only; it is not live Router integration.
"""

from __future__ import annotations

from backend.orchestration.router_port import (
    RESULT_SCHEMA_VERSION,
    RESULT_TYPE,
    AttentionTier,
    RoutingAssignment,
    RoutingResult,
    RoutingSnapshot,
)


def result_for(
    snapshot: RoutingSnapshot,
    assignments: tuple[RoutingAssignment, ...],
    **overrides: object,
) -> RoutingResult:
    """A Router result that answers ``snapshot``, so a test can vary one field at a time."""
    fields: dict[str, object] = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "result_type": RESULT_TYPE,
        "session_id": snapshot.session_id,
        "world_id": snapshot.world_id,
        "sequence": snapshot.sequence,
        "timestamp_ms": snapshot.timestamp_ms,
        "assignments": assignments,
    }
    return RoutingResult(**(fields | overrides))  # type: ignore[arg-type]


class RecordingRouter:
    """Assigns the active-conversation target to Focused and records every routed snapshot."""

    def __init__(self) -> None:
        self.routed: list[RoutingSnapshot] = []

    def route(self, snapshot: RoutingSnapshot) -> RoutingResult:
        self.routed.append(snapshot)
        conversation = snapshot.active_conversation
        target = conversation.target_npc_id if conversation else None
        return result_for(
            snapshot,
            tuple(
                RoutingAssignment(
                    npc_id=npc.npc_id,
                    tier=AttentionTier.FOCUSED if npc.npc_id == target else AttentionTier.AMBIENT,
                    previous_tier=None,
                    changed=True,
                )
                for npc in snapshot.npcs
            ),
        )

    def reset_session(self, session_id: str) -> None:
        return None


class RaisingRouter:
    def __init__(self, failure: Exception | None = None) -> None:
        self.failure = failure or RuntimeError("router exploded")

    def route(self, snapshot: RoutingSnapshot) -> RoutingResult:
        raise self.failure

    def reset_session(self, session_id: str) -> None:
        raise self.failure


class ScriptedRouter:
    """Returns whatever it was handed, so invalid Router output can be exercised."""

    def __init__(self, result: object) -> None:
        self.result = result

    def route(self, snapshot: RoutingSnapshot) -> RoutingResult:
        return self.result  # type: ignore[return-value]

    def reset_session(self, session_id: str) -> None:
        return None
