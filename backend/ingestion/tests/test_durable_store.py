"""Owner: Jerome & Richard"""

from __future__ import annotations

from pathlib import Path
from typing import AsyncIterator

import pytest
import pytest_asyncio

from backend.config import default_database_path
from backend.ingestion.durable_store import DurableStore, StorageUnavailable
from backend.ingestion.message_validation import validate_conversation_turn
from backend.ingestion.tests.canonical_messages import (
    CONVERSATION_ID,
    SESSION_ID,
    conversation_turn,
)
from backend.ingestion.turn_store import TurnStore

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


@pytest_asyncio.fixture
async def turns(tmp_path: Path) -> AsyncIterator[TurnStore]:
    store = DurableStore(tmp_path / "state" / "spotlight.sqlite3")
    await store.open()
    try:
        yield TurnStore(store)
    finally:
        await store.close()


def test_the_default_database_lives_outside_the_repository() -> None:
    assert not default_database_path().is_relative_to(REPOSITORY_ROOT)


async def test_the_database_and_its_directory_are_created_on_open(tmp_path: Path) -> None:
    store = DurableStore(tmp_path / "made" / "up" / "spotlight.sqlite3")

    await store.open()
    try:
        assert store.path.exists()
    finally:
        await store.close()


async def test_the_database_uses_write_ahead_logging(tmp_path: Path) -> None:
    store = DurableStore(tmp_path / "spotlight.sqlite3")
    await store.open()
    try:
        mode = list(await store.connection.execute_fetchall("PRAGMA journal_mode"))
        assert mode[0][0] == "wal"
    finally:
        await store.close()


async def test_a_closed_store_refuses_work_rather_than_losing_it(tmp_path: Path) -> None:
    store = DurableStore(tmp_path / "spotlight.sqlite3")

    with pytest.raises(StorageUnavailable):
        await TurnStore(store).record(validate_conversation_turn(conversation_turn()))


async def test_an_accepted_turn_is_stored_once_however_often_it_is_delivered(
    turns: TurnStore,
) -> None:
    turn = validate_conversation_turn(conversation_turn())

    assert await turns.record(turn) is True
    assert await turns.record(turn) is False
    assert await turns.record(turn) is False

    stored = await turns.recent(SESSION_ID, CONVERSATION_ID, limit=8)
    assert [one.turn_id for one in stored] == [turn.turn_id]


async def test_history_is_oldest_first_and_stops_before_the_triggering_turn(
    turns: TurnStore,
) -> None:
    for index in range(5):
        await turns.record(
            validate_conversation_turn(
                conversation_turn(turn_id=f"turn-{index}", turn_index=index)
            )
        )

    recent = await turns.recent(SESSION_ID, CONVERSATION_ID, limit=2, before_turn_index=4)

    assert [one.turn_index for one in recent] == [2, 3]
