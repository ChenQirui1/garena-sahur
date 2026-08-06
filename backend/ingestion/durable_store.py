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
        publication_status TEXT NOT NULL DEFAULT 'pending',
        published_at_ms INTEGER,
        UNIQUE (session_id, npc_id, command_sequence)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS provider_attempts (
        claim_key TEXT PRIMARY KEY,
        request_id TEXT NOT NULL,
        session_id TEXT NOT NULL,
        npc_id TEXT NOT NULL,
        started_at_ms INTEGER NOT NULL,
        outcome TEXT NOT NULL,
        request TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS conversation_sessions (
        session_id TEXT PRIMARY KEY,
        state TEXT NOT NULL,
        active_conversation_id TEXT,
        active_target_npc_id TEXT,
        unconfirmed_turn TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS conversation_threads (
        session_id TEXT NOT NULL,
        conversation_id TEXT NOT NULL,
        started_at_ms INTEGER NOT NULL,
        latest_turn_id TEXT,
        PRIMARY KEY (session_id, conversation_id)
    )
    """,
)

# Columns added after a database may already exist on a developer's machine. `CREATE TABLE IF
# NOT EXISTS` leaves an older table alone, so an additive column has to be applied separately or
# the first query against it fails on a machine that ran an earlier build.
ADDED_COLUMNS = (
    ("behaviour_commands", "publication_status", "TEXT NOT NULL DEFAULT 'pending'"),
    ("behaviour_commands", "published_at_ms", "INTEGER"),
)

# Every table holding durable per-session state, so explicit cleanup cannot miss one by being
# written before the table existed.
SESSION_TABLES = (
    "conversation_turns",
    "game_events",
    "behaviour_commands",
    "provider_attempts",
    "conversation_sessions",
    "conversation_threads",
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
        await _add_missing_columns(connection)
        await connection.commit()
        self._connection = connection

    async def close(self) -> None:
        if self._connection is None:
            return
        await self._connection.close()
        self._connection = None


async def _add_missing_columns(connection: aiosqlite.Connection) -> None:
    """Bring an existing database up to the current shape without touching its contents.

    Opening a database is never allowed to destroy evidence, so a missing column is added
    rather than the table being recreated.
    """
    for table, column, declaration in ADDED_COLUMNS:
        rows = await connection.execute_fetchall(f"PRAGMA table_info({table})")
        if any(row[1] == column for row in rows):
            continue
        await connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")
