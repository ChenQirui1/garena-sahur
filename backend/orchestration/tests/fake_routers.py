"""Contract fakes standing in for Elson & Daniel's Router at the owned port.

Owner: Jerome & Richard

Passing against these proves the owned handoff only; it is not live Router integration.
"""

from __future__ import annotations

from typing import Sequence

from backend.orchestration.router_port import AttentionTier, RoutingAssignment, RoutingSnapshot


class RecordingRouter:
    """Assigns the active-conversation NPC to Focused and records every routed snapshot."""

    def __init__(self) -> None:
        self.routed: list[RoutingSnapshot] = []

    def route(self, snapshot: RoutingSnapshot) -> tuple[RoutingAssignment, ...]:
        self.routed.append(snapshot)
        return tuple(
            RoutingAssignment(
                npc_id=candidate.npc_id,
                tier=(
                    AttentionTier.FOCUSED
                    if candidate.in_active_conversation
                    else AttentionTier.AMBIENT
                ),
            )
            for candidate in snapshot.candidates
        )

    def reset_session(self, session_id: str) -> None:
        return None


class RaisingRouter:
    def __init__(self, failure: Exception | None = None) -> None:
        self.failure = failure or RuntimeError("router exploded")

    def route(self, snapshot: RoutingSnapshot) -> Sequence[RoutingAssignment]:
        raise self.failure

    def reset_session(self, session_id: str) -> None:
        raise self.failure


class ScriptedRouter:
    """Returns whatever it was handed, so invalid Router output can be exercised."""

    def __init__(self, result: object) -> None:
        self.result = result

    def route(self, snapshot: RoutingSnapshot) -> Sequence[RoutingAssignment]:
        return self.result  # type: ignore[return-value]

    def reset_session(self, session_id: str) -> None:
        return None
