"""The limits Minecraft enforces, pinned at their boundaries.

Owner: Jerome & Richard

Every case here sits one character or one byte either side of a limit Ivan confirmed on
coordination issue #4 and the shipped mod enforces in `BehaviourCommand.parse` and
`WebSocketSubscriber`. `docs/message_schemas.md` §6 records none of them, so a command that
satisfies the document can still be refused on arrival; these are what stops the backend
producing one. Issue #60 proposes recording them in §6.

Lengths are asserted in the units the consumer counts. Java's `String.length()` counts UTF-16
code units, so the astral cases below are the ones that would pass a naive Python length check
and fail at the game.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import AsyncIterator

import pytest
import pytest_asyncio

from backend.orchestration.behaviour_command import (
    MAX_DIALOGUE_CHARACTERS,
    MAX_IDENTIFIER_CHARACTERS,
    MAX_PAYLOAD_BYTES,
    BehaviourCommand,
    CommandFieldOutOfBounds,
    PayloadTooLarge,
    bounded_dialogue,
    consumer_length,
    serialized_payload,
)
from backend.orchestration.observations import COMMAND_PAYLOAD_TOO_LARGE
from backend.orchestration.tests.harness import Harness, running, settings_for

# One astral character is two UTF-16 code units to the consumer and one character to Python.
ASTRAL = "\U0001f9d1"


def command(**overrides: object) -> BehaviourCommand:
    fields: dict[str, object] = {
        "session_id": "demo-01",
        "command_id": "command-322",
        "request_id": "request-0091",
        "npc_id": "shopkeeper-uuid",
        "tier": "focused",
        "event_id": None,
        "conversation_id": None,
        "turn_id": None,
        "source_sequence": 1842,
        "created_at_ms": 1_786_208_500_984,
        "expires_at_ms": 1_786_208_510_984,
        "dialogue": "Towards the fountain!",
        "action": None,
        "fallback_used": False,
        "command_sequence": 1,
    }
    fields.update(overrides)
    return BehaviourCommand(**fields)  # type: ignore[arg-type]


# ---- the unit the consumer counts in ------------------------------------------------


def test_an_astral_character_is_two_units_to_the_consumer_and_one_to_python() -> None:
    """The whole reason the bounds below are not `len`."""
    assert len(ASTRAL) == 1
    assert consumer_length(ASTRAL) == 2


# ---- dialogue -----------------------------------------------------------------------


def test_dialogue_at_the_limit_is_left_alone() -> None:
    at_limit = "a" * MAX_DIALOGUE_CHARACTERS

    assert bounded_dialogue(at_limit) == at_limit


def test_dialogue_one_character_past_the_limit_is_cut_to_it() -> None:
    bounded = bounded_dialogue("a" * (MAX_DIALOGUE_CHARACTERS + 1))

    assert consumer_length(bounded) == MAX_DIALOGUE_CHARACTERS
    assert bounded == "a" * MAX_DIALOGUE_CHARACTERS


def test_astral_dialogue_is_bounded_by_what_the_consumer_counts() -> None:
    """256 astral characters are 256 to Python and 512 to the mod, so this is already at the
    limit; 257 is over it despite Python reading it as half the allowance."""
    at_limit = ASTRAL * (MAX_DIALOGUE_CHARACTERS // 2)
    over = ASTRAL * (MAX_DIALOGUE_CHARACTERS // 2 + 1)

    assert bounded_dialogue(at_limit) == at_limit
    assert bounded_dialogue(over) == at_limit


def test_a_cut_never_splits_a_surrogate_pair() -> None:
    """An odd allowance has to leave one unit unused rather than emit half a character."""
    odd = "a" + ASTRAL * MAX_DIALOGUE_CHARACTERS

    bounded = bounded_dialogue(odd)

    assert consumer_length(bounded) == MAX_DIALOGUE_CHARACTERS - 1
    assert bounded.encode("utf-16-le").decode("utf-16-le") == bounded


def test_a_command_cannot_carry_dialogue_past_the_limit() -> None:
    """The bound belongs upstream, at the two places text is produced. This is the backstop that
    turns a missing bound into a failure instead of a command the game silently drops."""
    with pytest.raises(CommandFieldOutOfBounds, match="dialogue"):
        command(dialogue="a" * (MAX_DIALOGUE_CHARACTERS + 1))


def test_a_command_cannot_carry_empty_dialogue() -> None:
    """`docs/message_schemas.md` §6 allows `null`; the consumer refuses the empty string."""
    with pytest.raises(CommandFieldOutOfBounds, match="dialogue"):
        command(dialogue="")


def test_dialogue_at_the_limit_and_null_dialogue_are_both_accepted() -> None:
    assert command(dialogue="a" * MAX_DIALOGUE_CHARACTERS).dialogue is not None
    assert command(dialogue=None, action="stop").dialogue is None


# ---- identifiers --------------------------------------------------------------------

REQUIRED = ("session_id", "command_id", "request_id", "npc_id", "tier")
NULLABLE = ("event_id", "conversation_id", "turn_id")


@pytest.mark.parametrize("field", REQUIRED + NULLABLE)
def test_an_identifier_at_the_limit_is_accepted(field: str) -> None:
    assert getattr(command(**{field: "i" * MAX_IDENTIFIER_CHARACTERS}), field)


@pytest.mark.parametrize("field", REQUIRED + NULLABLE)
def test_an_identifier_one_character_past_the_limit_is_refused(field: str) -> None:
    with pytest.raises(CommandFieldOutOfBounds, match=field):
        command(**{field: "i" * (MAX_IDENTIFIER_CHARACTERS + 1)})


@pytest.mark.parametrize("field", REQUIRED + NULLABLE)
def test_an_empty_identifier_is_refused(field: str) -> None:
    """§ Common conventions: "IDs are stable, non-empty strings" — including the nullable ones,
    which the consumer accepts as absent but not as blank."""
    with pytest.raises(CommandFieldOutOfBounds, match=field):
        command(**{field: ""})


@pytest.mark.parametrize("field", NULLABLE)
def test_a_nullable_identifier_may_be_absent(field: str) -> None:
    assert getattr(command(**{field: None}), field) is None


@pytest.mark.parametrize("field", REQUIRED)
def test_a_required_identifier_may_not_be_absent(field: str) -> None:
    with pytest.raises(CommandFieldOutOfBounds, match=field):
        command(**{field: None})


def test_an_identifier_is_bounded_by_what_the_consumer_counts() -> None:
    """64 astral characters are 128 units, so 65 is over the bound at 65 Python characters."""
    assert command(session_id=ASTRAL * (MAX_IDENTIFIER_CHARACTERS // 2)).session_id

    with pytest.raises(CommandFieldOutOfBounds, match="session_id"):
        command(session_id=ASTRAL * (MAX_IDENTIFIER_CHARACTERS // 2 + 1))


# ---- payload size -------------------------------------------------------------------


def test_a_payload_inside_the_limit_serializes_to_the_bytes_that_will_be_sent() -> None:
    payload = serialized_payload(command())

    assert len(payload.encode()) <= MAX_PAYLOAD_BYTES
    assert json.loads(payload)["command_id"] == "command-322"


def test_a_payload_at_the_limit_is_accepted_and_one_byte_over_is_not() -> None:
    """Padding lands on `action`, which is the only way a command can approach this limit.

    Every identifier is capped at 128 and dialogue at 512, so the rest of the payload cannot add
    up to 64 KB however it is filled. `action` is the one text field no bound covers, and #60 is
    about to turn it into an object with a payload — which is when this stops being theoretical.
    """
    baseline = len(serialized_payload(command(action="a")).encode())
    room = MAX_PAYLOAD_BYTES - baseline

    at_limit = command(action="a" * (1 + room))
    assert len(serialized_payload(at_limit).encode()) == MAX_PAYLOAD_BYTES

    with pytest.raises(PayloadTooLarge):
        serialized_payload(command(action="a" * (2 + room)))


def test_the_size_is_measured_in_wire_bytes_rather_than_characters() -> None:
    """A payload of two-byte characters hits the limit at half the character count, so counting
    characters here would let a command through that the transport drops."""
    baseline = len(serialized_payload(command(action="a")).encode())
    characters = MAX_PAYLOAD_BYTES - baseline

    oversized = command(action="é" * characters)

    assert len(str(oversized.action)) < MAX_PAYLOAD_BYTES
    with pytest.raises(PayloadTooLarge):
        serialized_payload(oversized)


# ---- what an oversized command does to the running service --------------------------


@pytest_asyncio.fixture
async def harness(tmp_path: Path) -> AsyncIterator[Harness]:
    async for started in running(settings_for(tmp_path)):
        yield started


async def test_an_oversized_command_is_neither_sent_nor_stored(harness: Harness) -> None:
    """Not stored is the load-bearing half. A command committed and then refused would be found
    by the next restart and republished for as long as its lifetime allowed, so the size check
    has to happen before the insert rather than before the send.

    This is the backend declining to emit something unusable. Deciding whether to *apply* a
    command stays Minecraft's, per `docs/team-architecture.md` §9 rule 11.
    """
    oversized = command(action="a" * MAX_PAYLOAD_BYTES)

    delivered = await harness.pipeline.generation.publisher.publish(oversized)

    assert delivered is False
    assert harness.publisher.published == []
    assert await harness.pipeline.commands.stored(oversized.command_id) is None
    assert await harness.pipeline.commands.unpublished() == ()


async def test_an_oversized_command_is_observable_rather_than_silent(harness: Harness) -> None:
    oversized = command(action="a" * MAX_PAYLOAD_BYTES)

    await harness.pipeline.generation.publisher.publish(oversized)

    noted = harness.observed(COMMAND_PAYLOAD_TOO_LARGE)
    assert len(noted) == 1
    assert noted[0]["command_id"] == oversized.command_id
    assert noted[0]["npc_id"] == oversized.npc_id


async def test_a_command_inside_the_limit_still_goes_out(harness: Harness) -> None:
    """The two cases above have to be refusing the size, not refusing everything."""
    delivered = await harness.pipeline.generation.publisher.publish(command())

    assert delivered is True
    assert [one.command_id for one in harness.publisher.published] == ["command-322"]
