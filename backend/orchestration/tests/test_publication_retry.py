"""A command that cannot be delivered is retried on the specified cadence, then expires.

Owner: Jerome & Richard

The cadence is asserted as the exact list of delays the publisher asked for, which a manual
deadline records without any real time passing. Expiry is asserted against the command's own
15-second lifetime, which those same delays advance the manual clock through.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import AsyncIterator

import pytest
import pytest_asyncio

from backend.ingestion.tests.canonical_messages import (
    CONVERSATION_ID,
    SHOPKEEPER,
    TURN_ID,
    active_conversation,
)
from backend.orchestration.command_store import EXPIRED, PENDING, PUBLISHED
from backend.orchestration.conversation_manager import ConversationState
from backend.orchestration.observations import (
    COMMAND_PUBLICATION_EXPIRED,
    COMMAND_PUBLICATION_RETRIED,
)
from backend.orchestration.tests.harness import Harness, running, settings_for

CADENCE_MS = [100, 250, 500, 1_000]
COMMAND_LIFETIME_MS = 15_000


@pytest_asyncio.fixture
async def harness(tmp_path: Path) -> AsyncIterator[Harness]:
    async for started in running(settings_for(tmp_path)):
        yield started


async def status_of(harness: Harness, command_id: str) -> str:
    rows = await harness.pipeline.store.connection.execute_fetchall(
        "SELECT publication_status FROM behaviour_commands WHERE command_id = ?",
        (command_id,),
    )
    return str(list(rows)[0][0])


async def test_a_delivered_command_is_stored_before_it_is_sent_and_marked_published(
    harness: Harness,
) -> None:
    await harness.snapshot(active_conversation=active_conversation())
    await harness.turn()
    await harness.settle()

    command = harness.publisher.published[0]
    assert harness.publisher.stored_when_published == [command]
    assert await status_of(harness, command.command_id) == PUBLISHED
    assert harness.deadlines.slept_ms == [], "a first attempt that works waits for nothing"


async def test_a_refused_send_is_retried_on_the_specified_cadence(
    harness: Harness,
) -> None:
    harness.publisher.fail_next = 4

    await harness.snapshot(active_conversation=active_conversation())
    await harness.turn()
    await harness.settle()

    assert harness.deadlines.slept_ms == CADENCE_MS
    assert harness.publisher.attempts == 5
    assert len(harness.publisher.published) == 1
    assert [one["attempt"] for one in harness.observed(COMMAND_PUBLICATION_RETRIED)] == [
        1,
        2,
        3,
        4,
    ]


async def test_the_cadence_repeats_while_the_command_is_still_within_its_lifetime(
    harness: Harness,
) -> None:
    """One pass through the cadence spends 1.85 s, so a second pass must still be allowed."""
    harness.publisher.fail_next = 6

    await harness.snapshot(active_conversation=active_conversation())
    await harness.turn()
    await harness.settle()

    assert harness.deadlines.slept_ms == CADENCE_MS + [100, 250]
    assert len(harness.publisher.published) == 1


async def test_retries_stop_once_the_command_can_no_longer_be_accepted(
    harness: Harness,
) -> None:
    harness.publisher.fail_next = 1_000

    await harness.snapshot(active_conversation=active_conversation())
    await harness.turn()
    await harness.settle()

    assert harness.publisher.published == []
    assert sum(harness.deadlines.slept_ms) >= COMMAND_LIFETIME_MS
    assert sum(harness.deadlines.slept_ms) < COMMAND_LIFETIME_MS + max(CADENCE_MS)

    expired = harness.observed(COMMAND_PUBLICATION_EXPIRED)
    assert len(expired) == 1
    assert expired[0]["npc_id"] == SHOPKEEPER
    assert await status_of(harness, str(expired[0]["command_id"])) == EXPIRED


async def test_every_attempt_sends_byte_identical_content(harness: Harness) -> None:
    # The publisher refuses twice and then accepts, so three attempts carry the same command.
    harness.publisher.fail_next = 2

    await harness.snapshot(active_conversation=active_conversation())
    await harness.turn()
    await harness.settle()

    sent = harness.publisher.attempted_bytes
    assert len(sent) == 3
    assert len(set(sent)) == 1, "a retry must reuse the identical serialized command"

    # Identical bytes already imply identical fields; naming the ones the criterion lists
    # means a future payload change cannot quietly drop one and still look byte-stable.
    payload = json.loads(sent[0])
    assert payload["command_id"] and payload["command_sequence"] == 1
    assert payload["source_sequence"] == 1842
    assert (payload["turn_id"], payload["conversation_id"]) == (TURN_ID, CONVERSATION_ID)
    assert payload["expires_at_ms"] == payload["created_at_ms"] + COMMAND_LIFETIME_MS


async def test_publication_expiry_returns_an_active_conversation_to_engaged(
    harness: Harness,
) -> None:
    harness.publisher.fail_next = 1_000

    await harness.snapshot(active_conversation=active_conversation())
    await harness.turn()
    await harness.settle()

    assert harness.state() is ConversationState.ENGAGED
    assert len(harness.provider.started) == 1, "expiry must never regenerate"


async def test_publication_expiry_returns_a_closed_conversation_to_idle(
    tmp_path: Path,
) -> None:
    async for held in running(settings_for(tmp_path), gated=True):
        held.publisher.fail_next = 1_000
        await held.snapshot(active_conversation=active_conversation())
        await held.turn()
        await held.provider.wait_for_started(1)

        # Minecraft closes the conversation while the answer is still being generated.
        await held.snapshot(sequence=1843)
        await held.settle_routing()
        held.provider.release_all()
        await held.settle()

        assert held.state() is ConversationState.IDLE


async def test_an_expired_command_is_not_offered_to_recovery_again(
    harness: Harness,
) -> None:
    """`unpublished` drives restart recovery, so an expired row must not appear in it."""
    harness.publisher.fail_next = 1_000

    await harness.snapshot(active_conversation=active_conversation())
    await harness.turn()
    await harness.settle()

    assert await harness.pipeline.commands.unpublished() == ()
    rows = await harness.pipeline.store.connection.execute_fetchall(
        "SELECT COUNT(*) FROM behaviour_commands WHERE publication_status = ?", (PENDING,)
    )
    assert list(rows)[0][0] == 0
