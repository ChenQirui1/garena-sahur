"""Retain game-event revisions durably and rule on what each new revision may do.

Owner: Jerome & Richard

An event is a chain of complete-state revisions, so the store keeps every revision rather than
overwriting: the latest one decides whether the event is still active, and the earlier ones are
the evidence that the chain was unbroken. Delivery identity makes redelivery idempotent, while
anything that would make the chain incoherent — a gap, a conflicting body, time running
backwards, a revision after the event already finished — is refused rather than absorbed.

The witness set is frozen here because it is only knowable at the moment the event starts; later
revisions inherit it. The geometry that decides membership belongs to enrichment, so the caller
supplies the set and this module only guarantees it stops changing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable

from backend.ingestion.durable_store import DurableStore
from backend.ingestion.message_validation import (
    FIRST_EVENT_REVISION,
    GameEvent,
    validate_game_event,
)


class RevisionOutcome(StrEnum):
    APPLIED = "applied"
    DUPLICATE = "duplicate"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class RevisionResult:
    outcome: RevisionOutcome
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class StoredEvent:
    """The latest revision of one event, with the witness set frozen at its start."""

    event: GameEvent
    witnesses: frozenset[str]


class EventStore:
    def __init__(self, store: DurableStore) -> None:
        self._store = store

    async def record(self, event: GameEvent, witnesses: Iterable[str]) -> RevisionResult:
        """Commit one revision, or explain why the chain cannot accept it."""
        existing = await self._revision(event.session_id, event.event_id, event.event_revision)
        if existing is not None:
            if existing.event == event:
                return RevisionResult(RevisionOutcome.DUPLICATE)
            return RevisionResult(
                RevisionOutcome.REJECTED,
                f"revision {event.event_revision} of {event.event_id} conflicts with the"
                " revision already stored",
            )

        latest = await self.latest(event.session_id, event.event_id)
        rejection = _rejection(event, latest)
        if rejection is not None:
            return RevisionResult(RevisionOutcome.REJECTED, rejection)

        # A started revision fixes the witness set; every later revision inherits it.
        frozen = frozenset(witnesses) if latest is None else latest.witnesses
        await self._insert(event, frozen)
        return RevisionResult(RevisionOutcome.APPLIED)

    async def latest(self, session_id: str, event_id: str) -> StoredEvent | None:
        rows = await self._store.connection.execute_fetchall(
            "SELECT payload, witnesses FROM game_events"
            " WHERE session_id = ? AND event_id = ?"
            " ORDER BY event_revision DESC LIMIT 1",
            (session_id, event_id),
        )
        return next((_stored(row) for row in rows), None)

    async def active(self, session_id: str) -> tuple[StoredEvent, ...]:
        """Every event whose newest revision has not ended or been cancelled.

        Ordered by when each event started, then by identity, so `active_event_ids` and the
        relevance derived from it are the same on every call for the same stored state.
        """
        rows = await self._store.connection.execute_fetchall(
            "SELECT newest.payload, newest.witnesses FROM game_events AS newest"
            " JOIN (SELECT event_id, MAX(event_revision) AS revision,"
            "              MIN(timestamp_ms) AS started_at_ms"
            "       FROM game_events WHERE session_id = ? GROUP BY event_id) AS chain"
            "   ON newest.event_id = chain.event_id"
            "  AND newest.event_revision = chain.revision"
            " WHERE newest.session_id = ?"
            " ORDER BY chain.started_at_ms, newest.event_id",
            (session_id, session_id),
        )
        stored = (_stored(row) for row in rows)
        return tuple(one for one in stored if not one.event.is_terminal)

    async def _revision(
        self, session_id: str, event_id: str, revision: int
    ) -> StoredEvent | None:
        rows = await self._store.connection.execute_fetchall(
            "SELECT payload, witnesses FROM game_events"
            " WHERE session_id = ? AND event_id = ? AND event_revision = ?",
            (session_id, event_id, revision),
        )
        return next((_stored(row) for row in rows), None)

    async def _insert(self, event: GameEvent, witnesses: frozenset[str]) -> None:
        connection = self._store.connection
        await connection.execute(
            "INSERT INTO game_events"
            " (session_id, event_id, event_revision, message_id, timestamp_ms, status,"
            "  payload, witnesses) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event.session_id,
                event.event_id,
                event.event_revision,
                event.message_id,
                event.timestamp_ms,
                event.status,
                event.model_dump_json(),
                json.dumps(sorted(witnesses)),
            ),
        )
        await connection.commit()


def _rejection(event: GameEvent, latest: StoredEvent | None) -> str | None:
    """Describe why this revision cannot extend the chain, or ``None`` when it can."""
    if latest is None:
        if event.event_revision != FIRST_EVENT_REVISION:
            return (
                f"{event.event_id} starts at revision {event.event_revision};"
                f" the first revision must be {FIRST_EVENT_REVISION}"
            )
        return None

    if latest.event.is_terminal:
        return (
            f"{event.event_id} already {latest.event.status} at revision"
            f" {latest.event.event_revision}"
        )
    if event.event_revision != latest.event.event_revision + 1:
        return (
            f"{event.event_id} is at revision {latest.event.event_revision};"
            f" revision {event.event_revision} would leave a gap"
        )
    if event.timestamp_ms < latest.event.timestamp_ms:
        return (
            f"{event.event_id} revision {event.event_revision} is stamped"
            f" {event.timestamp_ms}, before revision {latest.event.event_revision}"
        )
    return None


def _stored(row: tuple[str, str]) -> StoredEvent:
    payload, witnesses = row
    return StoredEvent(
        event=validate_game_event(json.loads(payload)),
        witnesses=frozenset(json.loads(witnesses)),
    )
