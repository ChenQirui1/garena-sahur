"""Call one persistent Router from a serialized worker and fail closed.

Owner: Jerome & Richard
"""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from dataclasses import dataclass
from enum import StrEnum
from typing import Awaitable, Callable

from backend.orchestration.observations import ROUTING_RESULT_REJECTED, Observations
from backend.orchestration.router_port import (
    RESULT_SCHEMA_VERSION,
    RESULT_TYPE,
    AttentionTier,
    RouterPort,
    RoutingAssignment,
    RoutingDiagnostics,
    RoutingResult,
    RoutingSnapshot,
    TierCounts,
)

logger = logging.getLogger(__name__)


class RoutingStatus(StrEnum):
    ROUTED = "routed"
    ROUTER_FAILED = "router_failed"
    INVALID_RESULT = "invalid_result"
    STALE_RESULT = "stale_result"


@dataclass(frozen=True, slots=True)
class RoutingOutcome:
    """What the Router produced for one routed snapshot, or why nothing was produced.

    `counts` and `diagnostics` stay `None` when the Router sent none, so a consumer can tell
    "the Router did not report them" from "the Router reported them and they held". Today's
    Router reports neither, which is why absence is not a failure.
    """

    session_id: str
    world_id: str
    sequence: int
    status: RoutingStatus
    assignments: tuple[RoutingAssignment, ...] = ()
    failure_reason: str | None = None
    counts: TierCounts | None = None
    diagnostics: RoutingDiagnostics | None = None


class RouterHandoff:
    """Route the newest pending snapshot per session and world, one call at a time."""

    def __init__(self, router: RouterPort, observations: Observations) -> None:
        self._router = router
        self._observations = observations
        self._pending: dict[tuple[str, str], RoutingSnapshot] = {}
        self._outcomes: dict[tuple[str, str], RoutingOutcome] = {}
        self._routed_sequences: dict[tuple[str, str], int] = {}
        self._submitted = asyncio.Event()
        self._idle = asyncio.Event()
        self._idle.set()
        self._worker: asyncio.Task[None] | None = None
        self._listener: Callable[[RoutingOutcome], Awaitable[None]] | None = None

    @property
    def is_running(self) -> bool:
        return self._worker is not None and not self._worker.done()

    @property
    def is_idle(self) -> bool:
        return self._idle.is_set()

    def listen(self, listener: Callable[[RoutingOutcome], Awaitable[None]]) -> None:
        """Notify ``listener`` of every outcome the Router produced, whichever path routed it.

        Promotion, expiry, and demotion-cancellation are decided here and nowhere else, so a
        path that routed without notifying would consume the transition they read.
        """
        self._listener = listener

    async def start(self) -> None:
        if self.is_running:
            return
        self._worker = asyncio.create_task(self._route_pending_snapshots())

    async def stop(self) -> None:
        if self._worker is None:
            return
        self._worker.cancel()
        try:
            await self._worker
        except asyncio.CancelledError:
            pass
        self._worker = None

    def submit(self, snapshot: RoutingSnapshot) -> None:
        """Queue routing work, superseding any older snapshot still waiting."""
        key = (snapshot.session_id, snapshot.world_id)
        waiting = self._pending.get(key)
        if waiting is None or waiting.sequence < snapshot.sequence:
            self._pending[key] = snapshot
        self._idle.clear()
        self._submitted.set()

    async def route_now(self, snapshot: RoutingSnapshot) -> RoutingOutcome:
        """Route immediately for work that cannot proceed without the assignment.

        Snapshot refresh is coalesced on the worker; a conversation turn is not, because the
        turn's own generation decision needs this snapshot's tiers. The caller enriches before
        it gets here, and enrichment awaits, so by now the worker may already have routed a
        newer sequence for this session and world. What is guaranteed is only that the Router
        is never called concurrently and never called with a sequence behind one it has already
        answered: a snapshot the worker overtook is not routed at all, and its caller reads the
        newer outcome instead of failing closed.

        The same world sequence can still be routed twice — once on snapshot arrival and again
        when a turn changes the conversation projection. The handoff contract lists both as
        reasons to call the Router, so the enrichment differs even though the sequence does not.
        Whether a persistent Router treats the repeat as stale is a question for #3.
        """
        key = (snapshot.session_id, snapshot.world_id)
        outcome = await self._route_and_notify(key, snapshot)
        if outcome is not None:
            return outcome

        superseding = self._outcomes[key]
        logger.info(
            "sequence %s was superseded by %s during enrichment",
            snapshot.sequence,
            superseding.sequence,
        )
        return superseding

    def latest_outcome(self, session_id: str, world_id: str) -> RoutingOutcome | None:
        return self._outcomes.get((session_id, world_id))

    def reset_session(self, session_id: str) -> None:
        """Drop everything routing remembers about one session, including the Router's own.

        This is a direct call because the Router call itself never awaits, so it cannot land
        mid-route. Pending snapshots, outcomes, and routed sequences go first, so a snapshot
        queued before the reset cannot land on the freshly cleared state afterwards, and the
        session's next snapshot is not refused for being behind a sequence nobody remembers.
        """
        for key in [key for key in self._pending if key[0] == session_id]:
            self._pending.pop(key, None)
        for key in [key for key in self._outcomes if key[0] == session_id]:
            self._outcomes.pop(key, None)
        for key in [key for key in self._routed_sequences if key[0] == session_id]:
            self._routed_sequences.pop(key, None)
        self._router.reset_session(session_id)

    async def wait_until_idle(self) -> None:
        await self._idle.wait()

    async def _route_pending_snapshots(self) -> None:
        while True:
            await self._submitted.wait()
            self._submitted.clear()
            while self._pending:
                key, snapshot = self._pending.popitem()
                await self._route_and_notify(key, snapshot)
            self._idle.set()

    async def _route_and_notify(
        self, key: tuple[str, str], snapshot: RoutingSnapshot
    ) -> RoutingOutcome | None:
        """Route one snapshot and tell the listener, or ``None`` if a newer one already won.

        Both paths go through here so the listener cannot tell them apart, and so neither can
        hand the Router a sequence behind one it has already answered — a persistent Router
        rejects that, and the rejection would otherwise look like the Router being broken.
        """
        routed = self._routed_sequences.get(key)
        if routed is not None and snapshot.sequence < routed:
            return None

        self._routed_sequences[key] = snapshot.sequence
        outcome = self._route(snapshot)
        self._outcomes[key] = outcome
        self._discard_pending_through(key, snapshot.sequence)
        await self._notify(outcome)
        return outcome

    def _discard_pending_through(self, key: tuple[str, str], sequence: int) -> None:
        """Drop a waiting snapshot this routing overtook; it could no longer be routed."""
        waiting = self._pending.get(key)
        if waiting is not None and waiting.sequence <= sequence:
            self._pending.pop(key, None)

    async def _notify(self, outcome: RoutingOutcome) -> None:
        """Tell the listener, but never let it take the routing worker down with it.

        A worker that dies stops setting `is_idle`, so everything waiting on routing would wait
        for ever and the service would never report ready again.
        """
        if self._listener is None:
            return
        try:
            await self._listener(outcome)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("routing listener failed for session %s", outcome.session_id)

    def _route(self, snapshot: RoutingSnapshot) -> RoutingOutcome:
        try:
            result = self._router.route(snapshot)
        except Exception as failure:
            logger.exception("router call failed for session %s", snapshot.session_id)
            return self._failed(snapshot, RoutingStatus.ROUTER_FAILED, repr(failure))

        if isinstance(result, RoutingResult) and result.sequence != snapshot.sequence:
            reason = f"result answers sequence {result.sequence}, not {snapshot.sequence}"
            logger.warning("discarding a stale router result: %s", reason)
            return self._failed(snapshot, RoutingStatus.STALE_RESULT, reason)

        rejection = _reject_reason(result, snapshot)
        if rejection is not None:
            logger.error("router returned an invalid result: %s", rejection)
            return self._failed(snapshot, RoutingStatus.INVALID_RESULT, rejection)

        return RoutingOutcome(
            session_id=snapshot.session_id,
            world_id=snapshot.world_id,
            sequence=snapshot.sequence,
            status=RoutingStatus.ROUTED,
            assignments=tuple(result.assignments),
            counts=result.counts,
            diagnostics=result.diagnostics,
        )

    def _failed(
        self, snapshot: RoutingSnapshot, status: RoutingStatus, reason: str
    ) -> RoutingOutcome:
        """Produce no assignment, and say so where a demotion cannot be mistaken for it.

        Downstream, an NPC absent from a routed result has its queued work cancelled as a
        demotion. A result that never became assignments must therefore be visible as its own
        thing, or a Router defect and a deliberate demotion are the same silence.
        """
        self._observations.note(
            ROUTING_RESULT_REJECTED,
            session_id=snapshot.session_id,
            world_id=snapshot.world_id,
            sequence=snapshot.sequence,
            status=status,
            reason=reason,
        )
        return RoutingOutcome(
            session_id=snapshot.session_id,
            world_id=snapshot.world_id,
            sequence=snapshot.sequence,
            status=status,
            failure_reason=reason,
        )


def _reject_reason(result: object, snapshot: RoutingSnapshot) -> str | None:
    """Describe why a Router result cannot be trusted, or ``None`` when it can."""
    if not isinstance(result, RoutingResult):
        return "result is not a routing result"
    if result.schema_version != RESULT_SCHEMA_VERSION:
        return f"unsupported result schema_version {result.schema_version!r}"
    if result.result_type != RESULT_TYPE:
        return f"unexpected result_type {result.result_type!r}"
    if (result.session_id, result.world_id) != (snapshot.session_id, snapshot.world_id):
        return "result answers another session or world"
    if result.timestamp_ms != snapshot.timestamp_ms:
        return (
            f"result carries timestamp_ms {result.timestamp_ms}, not {snapshot.timestamp_ms}"
        )
    if not isinstance(result.assignments, (list, tuple)):
        return "assignments is not a sequence"

    candidate_ids = {npc.npc_id for npc in snapshot.npcs}
    assigned: set[str] = set()
    for assignment in result.assignments:
        if not isinstance(assignment, RoutingAssignment):
            return "result contains a value that is not a routing assignment"
        if not isinstance(assignment.tier, AttentionTier):
            return f"unknown tier for {assignment.npc_id}"
        if assignment.npc_id not in candidate_ids:
            return f"{assignment.npc_id} was not a candidate in the routed snapshot"
        if assignment.npc_id in assigned:
            return f"{assignment.npc_id} was assigned more than once"
        assigned.add(assignment.npc_id)

    omitted = candidate_ids - assigned
    if omitted:
        return f"no tier was assigned for {', '.join(sorted(omitted))}"

    return _reject_reported_totals_reason(result)


def _reject_reported_totals_reason(result: RoutingResult) -> str | None:
    """Describe why a result's own counts and diagnostics contradict its assignments.

    Every comparison here is a result against itself, using the capacities the Router chose to
    report. The backend neither reads `backend/router/config.py` nor decides a tier, so this
    checks the invariants `docs/message_schemas.md` §5 states without duplicating the capacity
    enforcement that produced them.

    Absence is not a rejection: today's Router reports neither field, and §5 states the
    invariants without saying the fields are required.
    """
    counts, diagnostics = result.counts, result.diagnostics
    if counts is not None and not isinstance(counts, TierCounts):
        return "counts is not a tier count"
    if diagnostics is not None and not isinstance(diagnostics, RoutingDiagnostics):
        return "diagnostics is not routing diagnostics"

    tallied = Counter(assignment.tier for assignment in result.assignments)

    if counts is not None:
        for tier, counted in (
            (AttentionTier.FOCUSED, counts.focused),
            (AttentionTier.REACTIVE, counts.reactive),
            (AttentionTier.AMBIENT, counts.ambient),
        ):
            if counted != tallied[tier]:
                return (
                    f"counts report {counted} {tier.value}, "
                    f"the assignments hold {tallied[tier]}"
                )
        if diagnostics is not None:
            total = counts.focused + counts.reactive + counts.ambient
            if total != diagnostics.candidate_count:
                return (
                    f"counts sum to {total}, not the routed "
                    f"candidate_count {diagnostics.candidate_count}"
                )

    if diagnostics is not None:
        for tier, capacity in (
            (AttentionTier.FOCUSED, diagnostics.focused_capacity),
            (AttentionTier.REACTIVE, diagnostics.reactive_capacity),
        ):
            if tallied[tier] > capacity:
                return f"{tallied[tier]} {tier.value} exceeds its capacity of {capacity}"

    return None
