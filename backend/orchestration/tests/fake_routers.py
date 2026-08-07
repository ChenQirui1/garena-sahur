"""Contract fakes standing in for Elson & Daniel's Router at the owned port.

Owner: Jerome & Richard

Passing against these proves the owned handoff only; it is not live Router integration.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import replace

from backend.orchestration.router_port import (
    RESULT_SCHEMA_VERSION,
    RESULT_TYPE,
    AttentionTier,
    RoutingAssignment,
    RoutingDiagnostics,
    RoutingNpc,
    RoutingResult,
    RoutingSnapshot,
    TierCounts,
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


class ReportingRouter(RecordingRouter):
    """Also reports the counts and diagnostics section 5 documents.

    The Router that exists today reports neither, so nothing else in the owned suite exercises
    the path where they are present and consistent.
    """

    focused_capacity = 2
    reactive_capacity = 6
    routing_time_ms = 0.31

    def route(self, snapshot: RoutingSnapshot) -> RoutingResult:
        result = super().route(snapshot)
        tallied = Counter(assignment.tier for assignment in result.assignments)
        return replace(
            result,
            counts=TierCounts(
                focused=tallied[AttentionTier.FOCUSED],
                reactive=tallied[AttentionTier.REACTIVE],
                ambient=tallied[AttentionTier.AMBIENT],
            ),
            diagnostics=RoutingDiagnostics(
                focused_capacity=self.focused_capacity,
                reactive_capacity=self.reactive_capacity,
                candidate_count=len(snapshot.npcs),
                routing_time_ms=self.routing_time_ms,
            ),
        )


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


class StaleSequence(ValueError):
    """What a persistent Router raises for a sequence older than the one it accepted.

    Declared here rather than imported from `backend/router/`: live Router integration is #10's,
    and mirroring the rule keeps the owned seam provable without depending on their module.
    """


class StatefulRouter(TierScriptRouter):
    """Also holds per-session-and-world sequence state, so an older snapshot is rejected.

    `backend/router/router.py` does two things a stateless stand-in hides: it rejects a strictly
    older sequence, and every accepted call overwrites its previous-tier record so the transition
    a promotion is decided from is consumed. `TierScriptRouter` already does the second.
    """

    def __init__(
        self,
        tiers: dict[str, AttentionTier] | None = None,
        default: AttentionTier = AttentionTier.AMBIENT,
    ) -> None:
        super().__init__(tiers, default)
        self._sequences: dict[tuple[str, str], int] = {}

    def route(self, snapshot: RoutingSnapshot) -> RoutingResult:
        key = (snapshot.session_id, snapshot.world_id)
        accepted = self._sequences.get(key)
        if accepted is not None and snapshot.sequence < accepted:
            raise StaleSequence(
                f"sequence {snapshot.sequence} is older than accepted sequence {accepted}"
            )
        self._sequences[key] = snapshot.sequence
        return super().route(snapshot)

    def reset_session(self, session_id: str) -> None:
        for key in [key for key in self._sequences if key[0] == session_id]:
            self._sequences.pop(key, None)
        super().reset_session(session_id)


class FlakyRouter:
    """Wraps another router and fails on demand, so a transient outage can be staged."""

    def __init__(self, inner: object, failing: bool = False) -> None:
        self.inner = inner
        self.failing = failing

    def route(self, snapshot: RoutingSnapshot) -> RoutingResult:
        if self.failing:
            raise RuntimeError("router is briefly unavailable")
        result: RoutingResult = self.inner.route(snapshot)  # type: ignore[attr-defined]
        return result

    def reset_session(self, session_id: str) -> None:
        return None


class OmittingRouter:
    """Wraps another router and drops one candidate's assignment on demand.

    `docs/message_schemas.md` §5 requires every candidate to appear exactly once, so this is a
    Router defect rather than a shape a real Router may produce. It exists because the defect is
    invisible from the result alone: the omission is only detectable against the snapshot that
    was routed.
    """

    def __init__(self, inner: object, omitted: str | None = None) -> None:
        self.inner = inner
        self.omitted = omitted

    def route(self, snapshot: RoutingSnapshot) -> RoutingResult:
        result: RoutingResult = self.inner.route(snapshot)  # type: ignore[attr-defined]
        if self.omitted is None:
            return result
        return replace(
            result,
            assignments=tuple(
                one for one in result.assignments if one.npc_id != self.omitted
            ),
        )

    def reset_session(self, session_id: str) -> None:
        self.inner.reset_session(session_id)  # type: ignore[attr-defined]


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
