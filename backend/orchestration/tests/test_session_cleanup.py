"""Erasing a session is something a caller asks for, and nothing else ever does it.

Owner: Jerome & Richard

Two halves to prove, and the second matters more than the first: that cleanup removes what it
says it removes, and that nothing removes it otherwise. A restart that quietly emptied a table
would satisfy every other test in the suite.
"""

from __future__ import annotations

from pathlib import Path
from typing import AsyncIterator
from urllib.parse import quote

import pytest
import pytest_asyncio

from backend.ingestion.tests.canonical_messages import (
    CONVERSATION_ID,
    SESSION_ID,
    SHOPKEEPER,
    active_conversation,
)
from backend.ingestion.turn_store import TurnStore
from backend.orchestration.conversation_manager import ConversationState
from backend.orchestration.observations import SESSION_CLEANED
from backend.orchestration.tests.fakes import ManualClock, RecordingPublisher
from backend.orchestration.tests.harness import (
    DurableCounts,
    Harness,
    running,
    settings_for,
)

OTHER_SESSION = "demo-02"

# Nothing constrains the characters in a session id, and both of these mean something to SQLite's
# `LIKE`: `_` matches any single character and `%` matches any run of them. `NEIGHBOUR_SESSION` is
# chosen to be matched by `WILDCARD_SESSION` read as a pattern, so a cleanup that pattern-matches
# rather than compares deletes evidence belonging to a session nobody asked about.
WILDCARD_SESSION = "demo_01%"
NEIGHBOUR_SESSION = "demoX01-other"

# What `seed` leaves behind: one turn, one event revision, and a command, a claim and an
# attempt for each of the two triggers it fires — the event reaction and the player turn.
SEEDED = DurableCounts(
    turns=1, events=1, commands=2, attempts=2, claims=2, sessions=1, threads=1
)
SEEDED_ROWS = sum(SEEDED)
EMPTY = DurableCounts(*(0,) * len(SEEDED))


class CountingRouter:
    """Records which sessions the Router was told to forget."""

    def __init__(self, inner: object) -> None:
        self._inner = inner
        self.reset: list[str] = []

    def route(self, snapshot: object) -> object:
        return self._inner.route(snapshot)  # type: ignore[attr-defined]

    def reset_session(self, session_id: str) -> None:
        self.reset.append(session_id)


@pytest_asyncio.fixture
async def harness(tmp_path: Path) -> AsyncIterator[Harness]:
    async for started in running(settings_for(tmp_path)):
        yield started


async def seed(harness: Harness) -> None:
    await harness.snapshot(active_conversation=active_conversation())
    await harness.event()
    await harness.turn()
    await harness.settle()


async def test_a_finished_session_leaves_evidence_behind_until_it_is_cleaned(
    harness: Harness,
) -> None:
    await seed(harness)

    assert await harness.durable_counts() == SEEDED


async def test_cleaning_a_session_removes_its_durable_state(harness: Harness) -> None:
    await seed(harness)

    response = await harness.client.delete(f"/sessions/{SESSION_ID}")
    await harness.settle()

    assert response.status_code == 200
    assert response.json()["session_id"] == SESSION_ID
    assert response.json()["rows_removed"] == SEEDED_ROWS
    assert await harness.durable_counts() == EMPTY
    assert harness.observed(SESSION_CLEANED)[0]["rows"] == SEEDED_ROWS


async def test_cleaning_one_session_leaves_another_untouched(harness: Harness) -> None:
    await seed(harness)
    await harness.snapshot(session_id=OTHER_SESSION, sequence=1)
    await harness.turn(session_id=OTHER_SESSION, turn_id="turn-100", turn_index=0)
    await harness.settle()

    before = await harness.durable_counts()
    await harness.client.delete(f"/sessions/{SESSION_ID}")

    after = await harness.durable_counts()
    assert (before.turns, after.turns) == (2, 1), "only the named session's turns are removed"
    turns = TurnStore(harness.pipeline.store)
    assert await turns.recent(SESSION_ID, CONVERSATION_ID, limit=10) == ()
    assert [
        one.session_id
        for one in await turns.recent(OTHER_SESSION, CONVERSATION_ID, limit=10)
    ] == [OTHER_SESSION]


async def test_cleaning_forgets_the_in_memory_state_as_well(harness: Harness) -> None:
    await seed(harness)

    await harness.client.delete(f"/sessions/{SESSION_ID}")

    assert harness.state() is ConversationState.IDLE
    assert harness.pipeline.intake.conversation.active_conversation(SESSION_ID) is None
    assert harness.pipeline.handoff.latest_outcome(SESSION_ID, "minecraft-overworld-market") is None


async def test_cleaning_resets_the_router_for_that_session_only(tmp_path: Path) -> None:
    """Router hysteresis and previous tiers must not outlive the session they describe."""
    from backend.orchestration.tests.fake_routers import RecordingRouter

    router = CountingRouter(RecordingRouter())
    async for started in running(settings_for(tmp_path), router):  # type: ignore[arg-type]
        await started.snapshot(active_conversation=active_conversation())
        await started.settle()

        await started.client.delete(f"/sessions/{SESSION_ID}")

        assert router.reset == [SESSION_ID]


async def test_a_cleaned_session_can_generate_for_the_same_turn_again(
    harness: Harness,
) -> None:
    """The durable claim is keyed by session, so cleanup must release it with everything else."""
    await seed(harness)
    calls_before = len(harness.provider.started)
    published_before = len(harness.published_for(SHOPKEEPER))

    await harness.client.delete(f"/sessions/{SESSION_ID}")
    await harness.snapshot(active_conversation=active_conversation())
    await harness.turn()
    await harness.settle()

    assert len(harness.provider.started) == calls_before + 1
    assert len(harness.published_for(SHOPKEEPER)) == published_before + 1


async def claimed_sessions(harness: Harness) -> list[str]:
    """Which sessions still hold a durable claim.

    `GenerationClaims` takes claims and never reports them, so there is no public reader to go
    through here. Naming the sessions keeps the assertion about which claim was released.
    """
    rows = await harness.pipeline.store.connection.execute_fetchall(
        "SELECT session_id FROM generation_claims ORDER BY session_id"
    )
    return [str(row[0]) for row in rows]


async def generate_for_session(harness: Harness, session_id: str, turn_id: str) -> None:
    """Drive one session far enough to leave a durable generation claim behind."""
    await harness.snapshot(session_id=session_id, active_conversation=active_conversation())
    await harness.turn(session_id=session_id, turn_id=turn_id)
    await harness.settle()


async def test_cleaning_a_session_whose_id_reads_as_a_pattern_spares_its_neighbour(
    harness: Harness,
) -> None:
    """A session id is compared, never matched: no character in one may mean anything."""
    await generate_for_session(harness, WILDCARD_SESSION, "turn-200")
    await generate_for_session(harness, NEIGHBOUR_SESSION, "turn-201")
    assert await claimed_sessions(harness) == [NEIGHBOUR_SESSION, WILDCARD_SESSION]

    await harness.client.delete(f"/sessions/{quote(WILDCARD_SESSION, safe='')}")

    assert await claimed_sessions(harness) == [
        NEIGHBOUR_SESSION
    ], "only the named session's claims are released"


async def test_restarting_without_cleanup_keeps_everything(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    publisher = RecordingPublisher()
    clock = ManualClock()

    async for first in running(settings, publisher=publisher, clock=clock):
        await seed(first)
        before = await first.durable_counts()

    async for second in running(settings, publisher=publisher, clock=clock):
        after = await second.durable_counts()

    assert before == SEEDED
    assert after == before, "a restart must not delete durable evidence"


async def test_cleaning_an_unknown_session_removes_nothing_and_does_not_fail(
    harness: Harness,
) -> None:
    await seed(harness)

    response = await harness.client.delete("/sessions/never-existed")

    assert response.status_code == 200
    assert response.json()["rows_removed"] == 0
    assert await harness.durable_counts() == SEEDED
