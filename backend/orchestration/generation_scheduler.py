"""Run queued generation work under the documented concurrency limits.

Owner: Jerome & Richard

The loop below is deliberately the only place that decides *when* work runs. What the work is,
how context is built, and what a provider returns all stay behind the executor, and the ordering
and capacity rules all stay in `generation_queue`.

Work is revalidated twice: once before the provider is called, so cancelled work never buys a
model call, and once after it returns, so output that arrived too late never reaches Minecraft.
The durable claim sits between the two, immediately before the call, which is where it already
sat when generation ran inline — superseded work therefore leaves no claim behind to block a
later retry of the same trigger.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Callable, Protocol

from backend.orchestration.behaviour_command import BehaviourCommand
from backend.orchestration.clock import Clock
from backend.orchestration.deduplication import GenerationClaims
from backend.orchestration.generation_policy import Generation, Trigger
from backend.orchestration.generation_queue import GenerationQueue
from backend.orchestration.observations import (
    WORK_CANCELLED,
    WORK_FAILED,
    WORK_REFUSED,
    WORK_SUPERSEDED,
    Observations,
)

logger = logging.getLogger(__name__)


class Executor(Protocol):
    """Everything the scheduler needs done to one piece of work, in order."""

    async def is_current(self, work: Generation) -> str | None: ...

    async def generate(self, work: Generation) -> BehaviourCommand | None: ...

    async def publish(self, command: BehaviourCommand) -> None: ...

    def abandon(self, work: Generation, reason: str) -> None: ...

    async def note_turn_not_generated(self, session_id: str) -> None: ...


class GenerationScheduler:
    def __init__(
        self,
        queue: GenerationQueue,
        claims: GenerationClaims,
        observations: Observations,
        clock: Clock,
    ) -> None:
        self._queue = queue
        self._claims = claims
        self._observations = observations
        self._clock = clock
        self._executor: Executor | None = None
        self._runnable = asyncio.Event()
        self._idle = asyncio.Event()
        self._idle.set()
        self._worker: asyncio.Task[None] | None = None
        self._running: set[asyncio.Task[None]] = set()

    def bind(self, executor: Executor) -> None:
        """Wire the executor after construction, because it also submits work back here."""
        self._executor = executor

    @property
    def is_running(self) -> bool:
        return self._worker is not None and not self._worker.done()

    @property
    def pending_count(self) -> int:
        return self._queue.pending_count

    async def start(self) -> None:
        if self.is_running:
            return
        self._worker = asyncio.create_task(self._dispatch_ready_work())

    async def stop(self) -> None:
        """Stop dispatching and let work in flight finish unwinding before the store closes.

        The dispatcher goes first. Cancelling the running tasks first leaves the worker free to
        start replacements while they unwind, and those replacements then run on into a closed
        database.
        """
        if self._worker is not None:
            self._worker.cancel()
            try:
                await self._worker
            except asyncio.CancelledError:
                pass
            self._worker = None

        running = tuple(self._running)
        for task in running:
            task.cancel()
        await asyncio.gather(*running, return_exceptions=True)

    async def drain(self) -> None:
        """Wait until nothing is queued and nothing is in flight."""
        await self._idle.wait()

    async def submit(self, work: Generation) -> None:
        """Offer one piece of work, and report whatever it displaced."""
        accepted = self._queue.enqueue(work)
        if accepted.superseded is not None:
            self._observations.note(
                WORK_SUPERSEDED,
                session_id=accepted.superseded.session_id,
                npc_id=accepted.superseded.npc_id,
                trigger=accepted.superseded.trigger.value,
                superseded_by=work.trigger.value,
            )
            await self._abandon(
                accepted.superseded, f"superseded by {work.trigger.value} work"
            )

        if not accepted.queued:
            self._observations.note(
                WORK_REFUSED,
                session_id=work.session_id,
                npc_id=work.npc_id,
                trigger=work.trigger.value,
                reason=accepted.refused,
            )
            return

        self._idle.clear()
        self._runnable.set()

    async def cancel(self, matches: Callable[[Generation], bool], reason: str) -> None:
        """Drop queued work that a later delivery has already invalidated."""
        for work in self._queue.cancel(matches):
            self._observations.note(
                WORK_CANCELLED,
                session_id=work.session_id,
                npc_id=work.npc_id,
                trigger=work.trigger.value,
                reason=reason,
            )
            await self._abandon(work, reason)
        self._settle()

    async def _dispatch_ready_work(self) -> None:
        while True:
            await self._runnable.wait()
            self._runnable.clear()
            while (work := self._queue.claim_next()) is not None:
                task = asyncio.create_task(self._run(work))
                self._running.add(task)
                task.add_done_callback(self._finished)
            self._settle()

    def _finished(self, task: asyncio.Task[None]) -> None:
        """Look for more work only once this task is genuinely gone, or `drain` would hang."""
        self._running.discard(task)
        self._runnable.set()

    async def _run(self, work: Generation) -> None:
        try:
            await self._execute(work)
        except asyncio.CancelledError:
            raise
        except Exception as failure:
            # A log alone is not enough: an exception swallowed here once hid a duplicate
            # command identity behind a test that looked green.
            logger.exception("generation work failed for %s", work.npc_id)
            self._observations.note(
                WORK_FAILED,
                session_id=work.session_id,
                npc_id=work.npc_id,
                trigger=work.trigger.value,
                reason=repr(failure),
            )
        finally:
            self._queue.release(work)

    async def _execute(self, work: Generation) -> None:
        executor = self._executor
        assert executor is not None, "the scheduler was started before it was bound"

        stale = await executor.is_current(work)
        if stale is not None:
            await self._abandon(work, stale)
            return

        if not await self._claims.claim(
            work.claim_key, work.session_id, self._clock.now_ms()
        ):
            await self._abandon(work, "generation was already claimed")
            return

        command = await executor.generate(work)
        if command is None:
            await self._abandon(work, "the provider produced nothing usable")
            return

        stale = await executor.is_current(work)
        if stale is not None:
            await self._abandon(work, f"late provider output discarded: {stale}")
            return

        await executor.publish(command)

    async def _abandon(self, work: Generation, reason: str) -> None:
        """Record why this work produced nothing, and release the conversation if it held it.

        A turn that was superseded by a newer turn must not release the conversation, because
        the replacement is still going to answer it. Asking the queue rather than tracking a
        flag keeps that decision in one place.
        """
        executor = self._executor
        assert executor is not None
        executor.abandon(work, reason)
        if work.trigger is Trigger.TURN and not self._queue.holds_trigger(
            work.session_id, Trigger.TURN, excluding=work
        ):
            await executor.note_turn_not_generated(work.session_id)

    def _settle(self) -> None:
        if self._queue.is_idle and not self._running:
            self._idle.set()
