"""Prevent repeated generation for the same NPC, event and conversation turn.

Owner: Jerome & Richard

The claim is durable and taken before the provider is called, so a redelivered turn cannot buy
a second model call — including across a restart, where an in-memory guard would have forgotten.

`ProviderAttempts` is the other half of the same guarantee and keys on the same claim: it records
that the call was *started*, so a process that dies mid-call leaves evidence behind. Without it,
recovery could not tell "never called" from "called, outcome unknown", and only one of those two
may be answered by calling the provider.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping

from backend.ingestion.durable_store import DurableStore

ATTEMPTED = "attempted"
SUCCEEDED = "succeeded"
FAILED = "failed"


class GenerationClaims:
    def __init__(self, store: DurableStore) -> None:
        self._store = store

    async def claim(self, key: str, at_ms: int) -> bool:
        """Take ``key`` for this caller; report ``False`` when it was already taken."""
        connection = self._store.connection
        cursor = await connection.execute(
            "INSERT OR IGNORE INTO generation_claims (claim_key, claimed_at_ms) VALUES (?, ?)",
            (key, at_ms),
        )
        await connection.commit()
        return cursor.rowcount == 1


@dataclass(frozen=True, slots=True)
class ProviderAttempt:
    """One started provider call and everything needed to answer it without calling again."""

    claim_key: str
    request_id: str
    session_id: str
    npc_id: str
    started_at_ms: int
    outcome: str
    request: dict[str, Any]


class ProviderAttempts:
    def __init__(self, store: DurableStore) -> None:
        self._store = store

    async def open(
        self, claim_key: str, request: Mapping[str, Any], started_at_ms: int
    ) -> None:
        """Commit that a call is about to be made, before it is made.

        A row that already exists is left alone: it means this claim was attempted once
        already, which is the fact worth keeping.
        """
        connection = self._store.connection
        await connection.execute(
            "INSERT OR IGNORE INTO provider_attempts"
            " (claim_key, request_id, session_id, npc_id, started_at_ms, outcome, request)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                claim_key,
                request["request_id"],
                request["session_id"],
                request["npc_id"],
                started_at_ms,
                ATTEMPTED,
                json.dumps(request),
            ),
        )
        await connection.commit()

    async def close(self, claim_key: str, outcome: str) -> None:
        """Record how a call ended, so recovery knows it does not have to answer it."""
        connection = self._store.connection
        await connection.execute(
            "UPDATE provider_attempts SET outcome = ? WHERE claim_key = ?",
            (outcome, claim_key),
        )
        await connection.commit()

    async def unresolved(self) -> tuple[ProviderAttempt, ...]:
        """Attempts that were started and never closed: the outcome is genuinely unknown."""
        rows = await self._store.connection.execute_fetchall(
            "SELECT claim_key, request_id, session_id, npc_id, started_at_ms, outcome, request"
            " FROM provider_attempts WHERE outcome = ? ORDER BY started_at_ms, claim_key",
            (ATTEMPTED,),
        )
        return tuple(
            ProviderAttempt(
                claim_key=row[0],
                request_id=row[1],
                session_id=row[2],
                npc_id=row[3],
                started_at_ms=row[4],
                outcome=row[5],
                request=json.loads(row[6]),
            )
            for row in rows
        )
