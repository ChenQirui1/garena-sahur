"""The restart-safe SQLite database the owned pipeline commits durable state to.

Owner: Jerome & Richard

The database lives outside the checkout so a fresh demo machine needs no provisioning step,
and write-ahead logging keeps a reader from blocking the intake commit that must land before a
message is acknowledged.
"""

from __future__ import annotations

from pathlib import Path

import aiosqlite

SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS conversation_turns (
        turn_id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        conversation_id TEXT NOT NULL,
        turn_index INTEGER NOT NULL,
        timestamp_ms INTEGER NOT NULL,
        speaker_type TEXT NOT NULL,
        speaker_id TEXT NOT NULL,
        target_npc_id TEXT NOT NULL,
        text TEXT NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS conversation_turns_in_order
        ON conversation_turns (session_id, conversation_id, turn_index)
    """,
    """
    CREATE TABLE IF NOT EXISTS game_events (
        session_id TEXT NOT NULL,
        event_id TEXT NOT NULL,
        event_revision INTEGER NOT NULL,
        message_id TEXT NOT NULL,
        timestamp_ms INTEGER NOT NULL,
        status TEXT NOT NULL,
        payload TEXT NOT NULL,
        witnesses TEXT NOT NULL,
        PRIMARY KEY (session_id, event_id, event_revision),
        UNIQUE (session_id, message_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS game_events_by_event
        ON game_events (session_id, event_id, event_revision)
    """,
    """
    CREATE TABLE IF NOT EXISTS generation_claims (
        claim_key TEXT PRIMARY KEY,
        claimed_at_ms INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS behaviour_commands (
        command_id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        npc_id TEXT NOT NULL,
        command_sequence INTEGER NOT NULL,
        created_at_ms INTEGER NOT NULL,
        expires_at_ms INTEGER NOT NULL,
        payload TEXT NOT NULL,
        UNIQUE (session_id, npc_id, command_sequence)
    )
    """,
)


class StorageUnavailable(RuntimeError):
    """State could not be read or written, so the message must not be acknowledged."""


class DurableStore:
    """One SQLite database per service lifecycle, created on first open."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._connection: aiosqlite.Connection | None = None

    @property
    def path(self) -> Path:
        return self._path

    @property
    def is_open(self) -> bool:
        return self._connection is not None

    @property
    def connection(self) -> aiosqlite.Connection:
        if self._connection is None:
            raise StorageUnavailable(f"durable store at {self._path} is not open")
        return self._connection

    async def open(self) -> None:
        if self._connection is not None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        connection = await aiosqlite.connect(self._path)
        await connection.execute("PRAGMA journal_mode=WAL")
        for statement in SCHEMA:
            await connection.execute(statement)
        await connection.commit()
        self._connection = connection

    async def close(self) -> None:
        if self._connection is None:
            return
        await self._connection.close()
        self._connection = None
