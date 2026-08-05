"""A relevant event revision becomes bounded mock reactions for the NPCs it concerns.

Owner: Jerome & Richard

Driven through the HTTP intake boundary, so the assertions are about observable behaviour:
intake outcome, what the Router was handed, which NPCs generated, and what the resulting
commands and facts carry.
"""

from __future__ import annotations

from pathlib import Path
from typing import AsyncIterator

import pytest
import pytest_asyncio

from backend.ingestion.tests.canonical_messages import (
    BEYOND_NEARBY_BAND,
    EVENT_ID,
    GUARD,
    SESSION_ID,
    SHOPKEEPER,
    THIEF,
    WITHIN_NEARBY_BAND,
    WITHIN_WITNESS_RADIUS,
    npc,
)
from backend.orchestration.tests.fake_routers import EventAwareRouter
from backend.orchestration.tests.harness import Harness, running, settings_for

BYSTANDER = "bystander-uuid"


def crowd() -> list[dict[str, object]]:
    """Four candidates: two named in the event, one in the nearby band, one well outside."""
    return [
        npc(SHOPKEEPER, position=dict(WITHIN_WITNESS_RADIUS)),
        npc(THIEF, position=dict(WITHIN_WITNESS_RADIUS)),
        npc(GUARD, position=dict(WITHIN_NEARBY_BAND)),
        npc(BYSTANDER, position=dict(BEYOND_NEARBY_BAND)),
    ]


@pytest_asyncio.fixture
async def harness(tmp_path: Path) -> AsyncIterator[Harness]:
    async for started in running(settings_for(tmp_path), EventAwareRouter()):
        yield started


async def seed(harness: Harness) -> None:
    await harness.snapshot(npcs=crowd(), candidate_count=4)


async def test_a_started_revision_is_accepted_and_retained(harness: Harness) -> None:
    await seed(harness)
    response = await harness.event()

    assert response.status_code == 202
    stored = await harness.pipeline.intake.events.active(SESSION_ID)
    assert [one.event.event_id for one in stored] == [EVENT_ID]
    assert stored[0].event.event_revision == 1


async def test_an_identical_redelivery_is_idempotent(harness: Harness) -> None:
    await seed(harness)
    first = await harness.event()
    second = await harness.event()

    assert first.status_code == 202
    assert second.status_code == 200
    assert second.json()["outcome"] == "duplicate"


async def test_a_conflicting_duplicate_revision_is_rejected(harness: Harness) -> None:
    await seed(harness)
    await harness.event()
    conflicting = await harness.event(event_type="market_brawl")

    assert conflicting.status_code == 422
    assert "conflict" in (conflicting.json()["detail"] or "")


async def test_the_first_revision_of_an_event_must_be_one(harness: Harness) -> None:
    await seed(harness)
    response = await harness.event(revision=2)

    assert response.status_code == 422


async def test_a_revision_gap_is_rejected(harness: Harness) -> None:
    await seed(harness)
    await harness.event(revision=1)
    response = await harness.event(revision=3)

    assert response.status_code == 422


async def test_an_updated_revision_continues_the_chain(harness: Harness) -> None:
    await seed(harness)
    await harness.event(revision=1)
    response = await harness.event(revision=2, status="updated")

    assert response.status_code == 202
    stored = await harness.pipeline.intake.events.active(SESSION_ID)
    assert stored[0].event.event_revision == 2
    assert stored[0].event.status == "updated"


async def test_a_revision_whose_timestamp_goes_backwards_is_rejected(
    harness: Harness,
) -> None:
    await seed(harness)
    first = await harness.event(revision=1)
    response = await harness.event(revision=2, status="updated", timestamp_ms=1)

    assert first.status_code == 202
    assert response.status_code == 422


@pytest.mark.parametrize("terminal", ["ended", "cancelled"])
async def test_no_revision_is_accepted_after_a_terminal_status(
    harness: Harness, terminal: str
) -> None:
    await seed(harness)
    await harness.event(revision=1)
    await harness.event(revision=2, status=terminal)
    response = await harness.event(revision=3, status="updated")

    assert response.status_code == 422
    assert await harness.pipeline.intake.events.active(SESSION_ID) == ()


@pytest.mark.parametrize("terminal", ["ended", "cancelled"])
async def test_a_terminal_revision_leaves_the_active_set(
    harness: Harness, terminal: str
) -> None:
    await seed(harness)
    await harness.event(revision=1)
    assert len(await harness.pipeline.intake.events.active(SESSION_ID)) == 1

    await harness.event(revision=2, status=terminal)
    assert await harness.pipeline.intake.events.active(SESSION_ID) == ()
