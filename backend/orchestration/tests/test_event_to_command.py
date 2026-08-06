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

from backend.ingestion.message_validation import validate_game_event
from backend.ingestion.tests.canonical_messages import (
    BEYOND_NEARBY_BAND,
    EVENT_ID,
    GUARD,
    SESSION_ID,
    SHOPKEEPER,
    THIEF,
    WITHIN_NEARBY_BAND,
    WITHIN_WITNESS_RADIUS,
    active_conversation,
    game_event,
    npc,
)
from backend.orchestration.development_router import AmbientOnlyRouter
from backend.orchestration.observations import EVENT_GENERATION_SUPPRESSED, NO_WORLD_STATE
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


async def test_a_redelivery_that_changed_its_content_is_rejected(harness: Harness) -> None:
    """Same delivery, different story: the publisher cannot rewrite a revision in place."""
    await seed(harness)
    await harness.event()
    conflicting = await harness.event(event_type="market_brawl")

    assert conflicting.status_code == 422
    assert "conflict" in (conflicting.json()["detail"] or "")


async def test_a_second_delivery_of_the_same_revision_is_rejected(harness: Harness) -> None:
    """A fresh delivery cannot re-open a revision the chain already settled."""
    await seed(harness)
    await harness.event()
    resent = await harness.event(message_id="event-message-001-again", event_type="market_brawl")

    assert resent.status_code == 422
    assert "conflicts with the revision already stored" in (resent.json()["detail"] or "")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("event_revision", 0),
        ("status", "paused"),
        ("position", None),
        ("schema_version", "2.0"),
    ],
)
async def test_an_invalid_event_is_rejected_before_it_is_stored(
    harness: Harness, field: str, value: object
) -> None:
    await seed(harness)
    response = await harness.ingest("game.event", game_event() | {field: value})

    assert response.status_code == 422
    assert await harness.pipeline.intake.events.active(SESSION_ID) == ()
    assert harness.publisher.published == []


async def test_a_delivery_identity_reused_for_other_content_is_rejected(
    harness: Harness,
) -> None:
    """§11.2 deduplicates by `message_id`, so one delivery cannot carry two revisions."""
    await seed(harness)
    await harness.event(revision=1)
    reused = await harness.event(revision=2, status="updated", message_id="event-message-001")

    assert reused.status_code == 422
    assert "conflict" in (reused.json()["detail"] or "")
    stored = await harness.pipeline.intake.events.active(SESSION_ID)
    assert stored[0].event.event_revision == 1


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


# --- Enrichment handed to the Router -------------------------------------------------------


async def test_each_role_carries_its_approved_relevance(harness: Harness) -> None:
    await seed(harness)
    await harness.event()

    assert harness.routed_npc(SHOPKEEPER).event_relevance == 1.0
    assert harness.routed_npc(THIEF).event_relevance == 1.0
    assert harness.routed_npc(GUARD).event_relevance == 0.8
    assert harness.routed_npc(BYSTANDER).event_relevance == 0.0


async def test_an_unrelated_npc_reports_no_roles_rather_than_a_role_named_unrelated(
    harness: Harness,
) -> None:
    await seed(harness)
    await harness.event()

    assert harness.routed_npc(BYSTANDER).event_roles == []


async def test_an_npc_holding_several_roles_takes_the_strongest(harness: Harness) -> None:
    """The shopkeeper is the victim and stood close enough to see it happen."""
    await seed(harness)
    await harness.event()

    routed = harness.routed_npc(SHOPKEEPER)
    assert routed.event_roles == ["target", "witness"]
    assert routed.event_relevance == 1.0


async def test_relevance_is_the_maximum_across_active_events(harness: Harness) -> None:
    await seed(harness)
    await harness.event()
    assert harness.routed_npc(BYSTANDER).event_relevance == 0.0

    await harness.event(
        event_id="stall-fire-002",
        message_id="event-message-002",
        actor_npc_ids=[],
        target_npc_ids=[],
        responder_npc_ids=[BYSTANDER],
    )

    # A role held in only the second event still counts, so aggregation spans both.
    assert harness.routed_npc(BYSTANDER).event_roles == ["responder"]
    assert harness.routed_npc(BYSTANDER).event_relevance == 0.8

    # The shopkeeper is the theft's target and merely nearby the fire: the strongest wins.
    shopkeeper = harness.routed_npc(SHOPKEEPER)
    assert shopkeeper.event_roles == ["target", "witness"]
    assert shopkeeper.event_relevance == 1.0

    assert harness.routed[-1].active_event_ids == [EVENT_ID, "stall-fire-002"]


async def test_an_event_that_ends_stops_contributing_relevance(harness: Harness) -> None:
    await seed(harness)
    await harness.event()
    assert harness.routed_npc(SHOPKEEPER).event_relevance == 1.0

    await harness.event(revision=2, status="ended")

    assert harness.routed[-1].active_event_ids == []
    assert harness.routed_npc(SHOPKEEPER).event_relevance == 0.0
    assert harness.routed_npc(SHOPKEEPER).event_roles == []


async def test_witness_membership_freezes_at_the_start_of_the_event(
    harness: Harness,
) -> None:
    """An NPC that walks in afterwards is nearby, but it did not see anything."""
    latecomer = "latecomer-uuid"
    await harness.snapshot(
        npcs=[*crowd(), npc(latecomer, position=dict(BEYOND_NEARBY_BAND))],
        candidate_count=5,
    )
    await harness.event()

    await harness.snapshot(
        sequence=1843,
        npcs=[*crowd(), npc(latecomer, position=dict(WITHIN_WITNESS_RADIUS))],
        candidate_count=5,
    )
    await harness.settle()

    routed = harness.routed_npc(latecomer)
    assert routed.event_roles == ["nearby"]
    assert routed.event_relevance == 0.2


async def test_a_witness_stays_a_witness_after_walking_away(harness: Harness) -> None:
    onlooker = "onlooker-uuid"
    await harness.snapshot(
        npcs=[*crowd(), npc(onlooker, position=dict(WITHIN_WITNESS_RADIUS))],
        candidate_count=5,
    )
    await harness.event()
    assert harness.routed_npc(onlooker).event_roles == ["witness"]

    await harness.snapshot(
        sequence=1843,
        npcs=[*crowd(), npc(onlooker, position=dict(BEYOND_NEARBY_BAND))],
        candidate_count=5,
    )
    await harness.settle()

    routed = harness.routed_npc(onlooker)
    assert routed.event_roles == ["witness"]
    assert routed.event_relevance == 0.4


async def test_a_later_revision_does_not_reopen_the_witness_set(harness: Harness) -> None:
    """The revision that could plausibly recompute it is the one that must not."""
    latecomer = "latecomer-uuid"
    onlooker = "onlooker-uuid"
    await harness.snapshot(
        npcs=[
            *crowd(),
            npc(latecomer, position=dict(BEYOND_NEARBY_BAND)),
            npc(onlooker, position=dict(WITHIN_WITNESS_RADIUS)),
        ],
        candidate_count=6,
    )
    await harness.event(revision=1)

    # The crowd rearranges completely, and only then does the event update.
    await harness.snapshot(
        sequence=1843,
        npcs=[
            *crowd(),
            npc(latecomer, position=dict(WITHIN_WITNESS_RADIUS)),
            npc(onlooker, position=dict(BEYOND_NEARBY_BAND)),
        ],
        candidate_count=6,
    )
    await harness.event(revision=2, status="updated")

    assert harness.routed_npc(latecomer).event_roles == ["nearby"]
    assert harness.routed_npc(onlooker).event_roles == ["witness"]


async def test_the_witness_radius_is_twelve_blocks(harness: Harness) -> None:
    """Pins the boundary itself, either side of it, measured from the event position."""
    inside, outside = "inside-uuid", "outside-uuid"
    await harness.snapshot(
        npcs=[
            *crowd(),
            npc(inside, position={"x": 104.2 + 11.9, "y": 64.0, "z": -31.8}),
            npc(outside, position={"x": 104.2 + 12.1, "y": 64.0, "z": -31.8}),
        ],
        candidate_count=6,
    )
    await harness.event()

    assert harness.routed_npc(inside).event_roles == ["witness"]
    assert harness.routed_npc(inside).event_relevance == 0.4
    assert harness.routed_npc(outside).event_roles == ["nearby"]
    assert harness.routed_npc(outside).event_relevance == 0.2


async def test_the_nearby_band_is_configurable(tmp_path: Path) -> None:
    """Shrinking the band below the guard's distance leaves an unrelated NPC unrelated."""
    settings = settings_for(tmp_path, nearby_radius_blocks=5.0)
    async for narrow in running(settings, EventAwareRouter()):
        await narrow.snapshot(
            npcs=[npc(BYSTANDER, position=dict(WITHIN_NEARBY_BAND))], candidate_count=1
        )
        await narrow.event(actor_npc_ids=[], target_npc_ids=[], responder_npc_ids=[])

        assert narrow.routed_npc(BYSTANDER).event_roles == []
        assert narrow.routed_npc(BYSTANDER).event_relevance == 0.0


async def test_the_active_conversation_target_is_interacting_now(harness: Harness) -> None:
    await harness.snapshot(
        npcs=crowd(), candidate_count=4, active_conversation=active_conversation()
    )
    await harness.event()

    assert harness.routed_npc(SHOPKEEPER).interaction_recency == 1.0


@pytest.mark.parametrize(
    ("elapsed_ms", "expected"),
    [
        (0, 0.9),
        (1_999, 0.9),
        (2_000, 0.7),
        (4_999, 0.7),
        (5_000, 0.4),
        (9_999, 0.4),
        (10_000, 0.2),
        (19_999, 0.2),
        (20_000, 0.0),
    ],
)
async def test_interaction_recency_decays_once_the_conversation_is_over(
    harness: Harness, elapsed_ms: int, expected: float
) -> None:
    """The full 1.0 belongs to an interaction happening now; ending it starts the decay."""
    await harness.snapshot(
        npcs=crowd(), candidate_count=4, active_conversation=active_conversation()
    )
    await harness.turn()

    harness.clock.advance(elapsed_ms)
    await harness.snapshot(sequence=1843, npcs=crowd(), candidate_count=4)
    await harness.settle()

    assert harness.routed_npc(SHOPKEEPER).interaction_recency == expected


async def test_recency_ignores_a_wall_clock_correction(harness: Harness) -> None:
    """Recency compares stamps only with each other, so NTP must not age an interaction."""
    await harness.snapshot(
        npcs=crowd(), candidate_count=4, active_conversation=active_conversation()
    )
    await harness.turn()

    harness.clock.advance(1_000)
    harness.clock.correct(60_000)
    await harness.snapshot(sequence=1843, npcs=crowd(), candidate_count=4)
    await harness.settle()

    assert harness.routed_npc(SHOPKEEPER).interaction_recency == 0.9


async def test_an_npc_the_player_never_addressed_has_no_recency(harness: Harness) -> None:
    await seed(harness)
    await harness.event()

    assert harness.routed_npc(GUARD).interaction_recency == 0.0


async def test_attention_edges_reach_the_router_unweighted(harness: Harness) -> None:
    await harness.snapshot(
        npcs=crowd(),
        candidate_count=4,
        attention_edges=[
            {
                "source_npc_id": GUARD,
                "target_npc_id": THIEF,
                "kind": "gaze",
                "active": True,
            }
        ],
    )
    await harness.event()

    assert [edge.model_dump() for edge in harness.routed[-1].attention_edges] == [
        {
            "source_npc_id": GUARD,
            "target_npc_id": THIEF,
            "kind": "gaze",
            "active": True,
        }
    ]


# --- Generation and suppression -------------------------------------------------------------


async def test_a_started_revision_generates_for_the_relevant_npcs_only(
    harness: Harness,
) -> None:
    await seed(harness)
    await harness.event()
    await harness.settle()

    reacted = [command.npc_id for command in harness.publisher.published]
    assert reacted == [SHOPKEEPER, THIEF, GUARD]
    assert harness.published_for(BYSTANDER) == []
    assert len(harness.telemetry.model_calls) == 3


async def test_an_updated_revision_generates_again(harness: Harness) -> None:
    await seed(harness)
    await harness.event(revision=1)
    await harness.settle()
    await harness.event(revision=2, status="updated")
    await harness.settle()

    assert len(harness.published_for(SHOPKEEPER)) == 2
    assert [command.command_sequence for command in harness.published_for(SHOPKEEPER)] == [1, 2]


@pytest.mark.parametrize("terminal", ["ended", "cancelled"])
async def test_a_terminal_revision_never_reaches_a_provider(
    harness: Harness, terminal: str
) -> None:
    await seed(harness)
    await harness.event(revision=1)
    await harness.settle()
    before = len(harness.telemetry.model_calls)

    await harness.event(revision=2, status=terminal)
    await harness.settle()

    assert len(harness.telemetry.model_calls) == before
    assert len(harness.published_for(SHOPKEEPER)) == 1
    assert {
        one["reason"] for one in harness.observed(EVENT_GENERATION_SUPPRESSED)
    } == {f"event {terminal}"}


async def test_the_same_revision_can_claim_generation_only_once(harness: Harness) -> None:
    """Redelivery that gets past intake is stopped by the durable generation claim."""
    await seed(harness)
    await harness.event()
    await harness.settle()

    await harness.pipeline.generation.on_event_revision(validate_game_event(game_event()))
    await harness.settle()

    assert len(harness.publisher.published) == 3
    assert len(harness.telemetry.model_calls) == 3
    assert [
        one["npc_id"]
        for one in harness.observed(EVENT_GENERATION_SUPPRESSED)
        if one["reason"] == "generation was already claimed"
    ] == [SHOPKEEPER, THIEF, GUARD]


async def test_ambient_npcs_stay_silent(tmp_path: Path) -> None:
    async for ambient in running(settings_for(tmp_path), AmbientOnlyRouter()):
        await ambient.snapshot(npcs=crowd(), candidate_count=4)
        await ambient.event()

        assert ambient.publisher.published == []
        assert ambient.telemetry.model_calls == []
        assert {
            one["reason"] for one in ambient.observed(EVENT_GENERATION_SUPPRESSED)
        } == {"npc is ambient"}


async def test_an_unrelated_npc_is_never_even_considered(harness: Harness) -> None:
    await seed(harness)
    await harness.event()

    assert [
        one["npc_id"] for one in harness.observed(EVENT_GENERATION_SUPPRESSED)
    ] == []
    assert harness.published_for(BYSTANDER) == []


async def test_a_reaction_command_retains_its_event_and_source_identities(
    harness: Harness,
) -> None:
    await seed(harness)
    await harness.event()
    await harness.settle()

    command = harness.published_for(SHOPKEEPER)[0]
    assert command.event_id == EVENT_ID
    assert command.conversation_id is None
    assert command.turn_id is None
    assert command.source_sequence == 1842
    assert command.session_id == SESSION_ID
    assert command.npc_id == SHOPKEEPER
    assert command.tier == "focused" or command.tier == "reactive"
    assert command.dialogue
    assert command.expires_at_ms == command.created_at_ms + 15_000


async def test_the_model_call_fact_retains_its_event_and_source_identities(
    harness: Harness,
) -> None:
    await seed(harness)
    await harness.event()
    await harness.settle()

    fact = next(
        one for one in harness.telemetry.model_calls if one.npc_id == SHOPKEEPER
    )
    assert fact.as_record()["record_type"] == "model_call"
    assert fact.event_id == EVENT_ID
    assert fact.conversation_id is None
    assert fact.turn_id is None
    assert fact.source_sequence == 1842
    assert fact.status == "success"
    assert fact.input_tokens > 0 and fact.output_tokens > 0


async def test_the_reaction_prompt_carries_the_npcs_own_involvement(
    harness: Harness,
) -> None:
    """The event reaches the model as a described trigger, not as a raw payload."""
    await seed(harness)
    await harness.event()
    await harness.settle()

    dialogue = harness.published_for(SHOPKEEPER)[0].dialogue or ""
    assert "market theft" in dialogue
    assert "It is happening to you." in dialogue


async def test_a_command_is_stored_before_each_reaction_is_published(
    harness: Harness,
) -> None:
    await seed(harness)
    await harness.event()

    assert harness.publisher.stored_when_published == harness.publisher.published


async def test_an_event_arriving_before_any_world_state_generates_nothing(
    harness: Harness,
) -> None:
    response = await harness.event()

    assert response.status_code == 202
    assert harness.publisher.published == []
    assert harness.observed(NO_WORLD_STATE) == [
        {"session_id": SESSION_ID, "event_id": EVENT_ID}
    ]
