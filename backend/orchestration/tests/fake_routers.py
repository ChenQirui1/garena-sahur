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
    RoutingNpc,
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
        return result_for(
            snapshot,
            tuple(
                RoutingAssignment(
                    npc_id=npc.npc_id,
                    tier=self.tier_for(snapshot, npc),
                    previous_tier=None,
                    changed=True,
                )
                for npc in snapshot.npcs
            ),
        )

    def tier_for(self, snapshot: RoutingSnapshot, npc: RoutingNpc) -> AttentionTier:
        conversation = snapshot.active_conversation
        target = conversation.target_npc_id if conversation else None
        return AttentionTier.FOCUSED if npc.npc_id == target else AttentionTier.AMBIENT

    def reset_session(self, session_id: str) -> None:
        return None


class EventAwareRouter(RecordingRouter):
    """Also promotes anything the backend marked event-relevant to Reactive.

    A stand-in, not a model of Elson & Daniel's Router: it reads the enrichment the backend
    supplies and applies the crudest possible rule, so a test can prove that relevance reached
    the port without this file acquiring scoring, capacity, or hysteresis behaviour.
    """

    def tier_for(self, snapshot: RoutingSnapshot, npc: RoutingNpc) -> AttentionTier:
        tier = super().tier_for(snapshot, npc)
        if tier is AttentionTier.AMBIENT and npc.event_relevance > 0:
            return AttentionTier.REACTIVE
        return tier


class TierScriptRouter:
    """Assigns whatever tier the test currently says, and reports the transition honestly.

    Real promotion and demotion come from Elson & Daniel's hysteresis, which the backend must
    not reproduce. A test that needs a specific transition therefore states it directly here
    rather than trying to provoke one through enrichment.
    """

    def __init__(
        self,
        tiers: dict[str, AttentionTier] | None = None,
        default: AttentionTier = AttentionTier.AMBIENT,
    ) -> None:
        self.tiers = dict(tiers or {})
        self.default = default
        self.routed: list[RoutingSnapshot] = []
        self._previous: dict[str, AttentionTier] = {}

    def route(self, snapshot: RoutingSnapshot) -> RoutingResult:
        self.routed.append(snapshot)
        assignments = []
        for npc in snapshot.npcs:
            tier = self.tiers.get(npc.npc_id, self.default)
            previous = self._previous.get(npc.npc_id)
            assignments.append(
                RoutingAssignment(
                    npc_id=npc.npc_id,
                    tier=tier,
                    previous_tier=previous,
                    changed=previous is not None and previous is not tier,
                )
            )
            self._previous[npc.npc_id] = tier
        return result_for(snapshot, tuple(assignments))

    def reset_session(self, session_id: str) -> None:
        self._previous.clear()


class FlakyRouter:
    """Wraps another router and fails on demand, so a transient outage can be staged."""

    def __init__(self, inner: object, failing: bool = False) -> None:
        self.inner = inner
        self.failing = failing

    def route(self, snapshot: RoutingSnapshot) -> RoutingResult:
        if self.failing:
            raise RuntimeError("router is briefly unavailable")
        return self.inner.route(snapshot)  # type: ignore[attr-defined]

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
