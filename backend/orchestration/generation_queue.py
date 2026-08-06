"""Decide which pending generation runs next, and how much runs at once.

Owner: Jerome & Richard

Nothing here awaits, stores, or calls a provider: it is the ordering and capacity rules alone,
so what "Focused first, first-in-first-out within a tier, two Focused, six Reactive, eight total,
one in flight per NPC" means can be read in one place.

One NPC holds at most one pending slot for work a later delivery can restate — an event
reaction, a promotion, an expiry. That is ADR 0007's single-slot rule, which reserved itself for
this ticket: it is what makes a newer turn supersede pending event or promotion work, and it is
also why a burst of world snapshots cannot grow an unbounded queue.

Player turns are not coalesced. Each one is a distinct utterance the player is owed an answer
to, specification #1 supersedes only "pending event or promotion work", and turns are already
deduplicated durably by turn identity, so they queue behind each other in arrival order instead.

Work already in flight is never evicted here — a spent model call is discarded by revalidation
before publication instead, so the provider attempt stays visible in telemetry rather than
disappearing.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Callable

from backend.orchestration.generation_policy import Generation, Trigger
from backend.orchestration.router_port import AttentionTier

TIER_PRIORITY = {AttentionTier.FOCUSED: 0, AttentionTier.REACTIVE: 1}

# Work a later delivery simply restates, so only its newest form is worth keeping.
COALESCED = frozenset({Trigger.EVENT, Trigger.PROMOTION, Trigger.EXPIRY})


@dataclass(frozen=True, slots=True)
class Enqueued:
    """What accepting one piece of work did to the queue."""

    queued: bool
    superseded: Generation | None = None
    refused: str | None = None


@dataclass(frozen=True, slots=True)
class _Waiting:
    generation: Generation
    ordinal: int


class GenerationQueue:
    def __init__(self, focused_limit: int, reactive_limit: int, total_limit: int) -> None:
        self._limits = {
            AttentionTier.FOCUSED: focused_limit,
            AttentionTier.REACTIVE: reactive_limit,
        }
        self._total_limit = total_limit
        self._waiting: list[_Waiting] = []
        self._in_flight: dict[tuple[str, str], Generation] = {}
        self._in_flight_tiers: Counter[AttentionTier] = Counter()
        self._claimed: set[str] = set()
        self._arrivals = 0

    @property
    def pending_count(self) -> int:
        return len(self._waiting)

    @property
    def in_flight_count(self) -> int:
        return len(self._in_flight)

    @property
    def is_idle(self) -> bool:
        return not self._waiting and not self._in_flight

    def enqueue(self, generation: Generation) -> Enqueued:
        """Take one piece of work, evicting the restatable work it supersedes for that NPC."""
        if generation.claim_key in self._claimed:
            return Enqueued(queued=False, refused="the same work is already pending")

        superseded = self._evict(
            lambda waiting: _slot(waiting.generation) == _slot(generation)
            and waiting.generation.trigger in COALESCED
        )
        self._arrivals += 1
        self._waiting.append(_Waiting(generation, self._arrivals))
        self._claimed.add(generation.claim_key)
        return Enqueued(queued=True, superseded=superseded[0] if superseded else None)

    def claim_next(self) -> Generation | None:
        """The highest-priority work that may start now, or ``None`` while nothing may."""
        if len(self._in_flight) >= self._total_limit:
            return None

        startable = [
            waiting
            for waiting in self._waiting
            if _slot(waiting.generation) not in self._in_flight
            and self._has_room(waiting.generation.tier)
        ]
        if not startable:
            return None

        chosen = min(startable, key=_dispatch_order)
        self._waiting.remove(chosen)
        generation = chosen.generation
        self._in_flight[_slot(generation)] = generation
        self._in_flight_tiers[generation.tier] += 1
        return generation

    def release(self, generation: Generation) -> None:
        """Give back the slot one finished piece of work held."""
        self._in_flight.pop(_slot(generation), None)
        self._in_flight_tiers[generation.tier] -= 1
        self._claimed.discard(generation.claim_key)

    def cancel(self, matches: Callable[[Generation], bool]) -> tuple[Generation, ...]:
        """Drop every waiting item ``matches`` accepts, and report what was dropped."""
        return self._evict(lambda waiting: matches(waiting.generation))

    def holds_trigger(
        self, session_id: str, trigger: Trigger, excluding: Generation | None = None
    ) -> bool:
        """Whether this session has *other* work of ``trigger`` waiting or in flight.

        ``excluding`` is how a piece of work asks whether anything besides itself is still
        going to answer, which it cannot tell from its own in-flight entry.
        """
        return any(
            generation.session_id == session_id
            and generation.trigger is trigger
            and generation is not excluding
            for generation in (
                *(waiting.generation for waiting in self._waiting),
                *self._in_flight.values(),
            )
        )

    def _evict(self, matches: Callable[[_Waiting], bool]) -> tuple[Generation, ...]:
        doomed = [waiting for waiting in self._waiting if matches(waiting)]
        for waiting in doomed:
            self._waiting.remove(waiting)
            self._claimed.discard(waiting.generation.claim_key)
        return tuple(waiting.generation for waiting in doomed)

    def _has_room(self, tier: AttentionTier) -> bool:
        limit = self._limits.get(tier)
        return limit is not None and self._in_flight_tiers[tier] < limit


def _slot(generation: Generation) -> tuple[str, str]:
    return (generation.session_id, generation.npc_id)


def _dispatch_order(waiting: _Waiting) -> tuple[int, int]:
    """Focused before Reactive, and within a tier the one that arrived first.

    The ordinal is an arrival counter rather than a timestamp, because two pieces of work
    accepted in the same millisecond still need a defined order.
    """
    return (TIER_PRIORITY[waiting.generation.tier], waiting.ordinal)
