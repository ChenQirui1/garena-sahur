"""Call one persistent Router from a serialized worker and fail closed.

Owner: Jerome & Richard
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from enum import StrEnum

from backend.orchestration.router_port import (
    AttentionTier,
    RouterPort,
    RoutingAssignment,
    RoutingSnapshot,
)

logger = logging.getLogger(__name__)


class RoutingStatus(StrEnum):
    ROUTED = "routed"
    ROUTER_FAILED = "router_failed"
    INVALID_RESULT = "invalid_result"


@dataclass(frozen=True, slots=True)
class RoutingOutcome:
    """What the Router produced for one routed snapshot, or why nothing was produced."""

    session_id: str
    world_id: str
    source_sequence: int
    status: RoutingStatus
    assignments: tuple[RoutingAssignment, ...] = ()
    failure_reason: str | None = None


class RouterHandoff:
    """Route the newest pending snapshot per session and world, one call at a time."""

    def __init__(self, router: RouterPort) -> None:
        self._router = router
        self._pending: dict[tuple[str, str], RoutingSnapshot] = {}
        self._outcomes: dict[tuple[str, str], RoutingOutcome] = {}
        self._submitted = asyncio.Event()
        self._idle = asyncio.Event()
        self._idle.set()
        self._worker: asyncio.Task[None] | None = None

    @property
    def is_running(self) -> bool:
        return self._worker is not None and not self._worker.done()

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
        if waiting is None or waiting.source_sequence < snapshot.source_sequence:
            self._pending[key] = snapshot
        self._idle.clear()
        self._submitted.set()

    def latest_outcome(self, session_id: str, world_id: str) -> RoutingOutcome | None:
        return self._outcomes.get((session_id, world_id))

    async def wait_until_idle(self) -> None:
        await self._idle.wait()

    async def _route_pending_snapshots(self) -> None:
        while True:
            await self._submitted.wait()
            self._submitted.clear()
            while self._pending:
                key, snapshot = self._pending.popitem()
                self._outcomes[key] = self._route(snapshot)
            self._idle.set()

    def _route(self, snapshot: RoutingSnapshot) -> RoutingOutcome:
        try:
            assignments = self._router.route(snapshot)
        except Exception as failure:
            logger.exception("router call failed for session %s", snapshot.session_id)
            return self._failed(snapshot, RoutingStatus.ROUTER_FAILED, repr(failure))

        rejection = _reject_reason(assignments, snapshot)
        if rejection is not None:
            logger.error("router returned an invalid result: %s", rejection)
            return self._failed(snapshot, RoutingStatus.INVALID_RESULT, rejection)

        return RoutingOutcome(
            session_id=snapshot.session_id,
            world_id=snapshot.world_id,
            source_sequence=snapshot.source_sequence,
            status=RoutingStatus.ROUTED,
            assignments=tuple(assignments),
        )

    @staticmethod
    def _failed(
        snapshot: RoutingSnapshot, status: RoutingStatus, reason: str
    ) -> RoutingOutcome:
        return RoutingOutcome(
            session_id=snapshot.session_id,
            world_id=snapshot.world_id,
            source_sequence=snapshot.source_sequence,
            status=status,
            failure_reason=reason,
        )


def _reject_reason(assignments: object, snapshot: RoutingSnapshot) -> str | None:
    """Describe why a Router result cannot be trusted, or ``None`` when it can."""
    if not isinstance(assignments, (list, tuple)):
        return "result is not a sequence of assignments"

    candidate_ids = {candidate.npc_id for candidate in snapshot.candidates}
    assigned: set[str] = set()
    for assignment in assignments:
        if not isinstance(assignment, RoutingAssignment):
            return "result contains a value that is not a routing assignment"
        if not isinstance(assignment.tier, AttentionTier):
            return f"unknown tier for {assignment.npc_id}"
        if assignment.npc_id not in candidate_ids:
            return f"{assignment.npc_id} was not a candidate in the routed snapshot"
        if assignment.npc_id in assigned:
            return f"{assignment.npc_id} was assigned more than once"
        assigned.add(assignment.npc_id)

    return None
