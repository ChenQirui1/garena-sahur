"""Prevent repeated generation for the same NPC, event and conversation turn.

Owner: Jerome & Richard

The claim is durable and taken before the provider is called, so a redelivered turn cannot buy
a second model call — including across a restart, where an in-memory guard would have forgotten.
"""

from __future__ import annotations

from backend.ingestion.durable_store import DurableStore


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
