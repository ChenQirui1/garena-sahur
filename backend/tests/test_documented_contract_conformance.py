"""Our boundary types against `docs/message_schemas.md`.

Owner: Jerome & Richard

Issue #5 drifted without a single test failing: the field names were designed from prose, and the
fixtures were written from the same misunderstanding as the code, so everything agreed with
everything except the team. These cases compare against the tracked document itself, which is the
only party to that disagreement a fixture cannot silently join.

Each case compares on the axis by which the shape actually reaches the wire. `behaviour.command`
is the reason that matters: its dataclass carries no `schema_version` or `message_type` because
`as_payload` adds them at serialisation, so comparing the dataclass invents two failures that do
not exist.

Every delta is declared below with its reason. A fourth appearing is a finding.
"""

from __future__ import annotations

import dataclasses

import pytest

from backend.context.npc_profiles import NpcProfile
from backend.ingestion.message_validation import (
    validate_conversation_turn,
    validate_game_event,
    validate_world_snapshot,
)
from backend.ingestion.message_validation import (
    ConversationTurn,
    GameEvent,
    WorldSnapshot,
)
from backend.orchestration.behaviour_command import BehaviourCommand
from backend.orchestration.router_port import RoutingResult, RoutingSnapshot
from backend.orchestration.telemetry_port import ModelCallFact
from backend.tests.tracked_documents import documented_keys, documented_payload

WORLD_SNAPSHOT = "1. `world.snapshot`"
GAME_EVENT = "2. `game.event`"
CONVERSATION_TURN = "3. `conversation.turn`"
ROUTING_SNAPSHOT = "4. Backend-to-Router `routing_snapshot`"
ROUTING_RESULT = "5. Router `routing_result`"
BEHAVIOUR_COMMAND = "6. `behaviour.command`"
TELEMETRY_RECORD = "7. `telemetry.record`"
NPC_PROFILES = "8. Backend-local NPC profiles"

# The document describes `command_sequence` in §6's prose as a provisional extension, pending the
# shared ordering decision under #4. It is emitted deliberately and is not drift.
COMMAND_EXTENSIONS = {"command_sequence"}

# Envelope fields belong to whoever serialises the record. The tracked ownership tree assigns
# `backend/telemetry/` to Elson & Daniel; we hand over the payload, not the envelope.
FACT_ENVELOPE = {"schema_version", "record_type"}

# Backend-local state telling an authored persona from the safe generic profile an unknown NPC
# receives. §8 documents the stored document, and this is never in it.
PROFILE_LOCAL_FIELDS = {"authored"}


def field_names(model: type) -> set[str]:
    if hasattr(model, "model_fields"):
        return set(model.model_fields)
    return {field.name for field in dataclasses.fields(model)}


@pytest.mark.parametrize(
    "section, model",
    [
        (WORLD_SNAPSHOT, WorldSnapshot),
        (GAME_EVENT, GameEvent),
        (CONVERSATION_TURN, ConversationTurn),
        (ROUTING_SNAPSHOT, RoutingSnapshot),
        (ROUTING_RESULT, RoutingResult),
    ],
    ids=["world.snapshot", "game.event", "conversation.turn", "routing_snapshot", "routing_result"],
)
def test_the_documented_key_set_is_exactly_the_models(section: str, model: type) -> None:
    documented, ours = documented_keys(section), field_names(model)

    assert documented - ours == set(), f"documented but absent from {model.__name__}"
    assert ours - documented == set(), f"carried by {model.__name__} but undocumented"


@pytest.mark.parametrize(
    "section, validate",
    [
        (WORLD_SNAPSHOT, validate_world_snapshot),
        (GAME_EVENT, validate_game_event),
        (CONVERSATION_TURN, validate_conversation_turn),
    ],
    ids=["world.snapshot", "game.event", "conversation.turn"],
)
def test_the_documented_example_is_accepted_by_our_validator(section: str, validate) -> None:
    """Stronger than key comparison: the document's own payload survives our rules.

    A field we kept but constrained more tightly than the team does would pass the key check and
    fail here, which is exactly the shape of the bound that #5 invented.

    Only the three inbound messages get this. §4 and §5 describe shapes that cross an in-process
    call to the Router rather than a wire — `RoutingResult` is a plain dataclass Elson & Daniel's
    implementation returns, never JSON we parse — so key conformance is the whole of what can be
    asserted there.
    """
    accepted = validate(documented_payload(section))

    assert accepted is not None


def test_the_command_we_emit_carries_every_documented_field() -> None:
    """Compared against the emitted payload; the dataclass is not the wire shape."""
    emitted = set(_a_command().as_payload())
    documented = documented_keys(BEHAVIOUR_COMMAND)

    assert documented - emitted == set(), "documented but never emitted"
    assert emitted - documented == COMMAND_EXTENSIONS, "undeclared extension on the command"


def test_the_model_call_fact_carries_every_documented_field_but_the_envelope() -> None:
    ours = field_names(ModelCallFact) | {"latency_ms"}
    documented = documented_keys(TELEMETRY_RECORD)

    assert documented - ours == FACT_ENVELOPE, "documented but absent from the fact"
    assert ours - documented == set(), "carried by the fact but undocumented"


def test_the_profile_model_matches_the_documented_local_document() -> None:
    documented, ours = documented_keys(NPC_PROFILES), field_names(NpcProfile)

    assert documented - ours == set(), "documented but absent from NpcProfile"
    assert ours - documented == PROFILE_LOCAL_FIELDS, "undeclared field on NpcProfile"


def test_latency_is_derived_rather_than_stored() -> None:
    """The document lists `latency_ms` as a field, so the allowance above has to be real."""
    assert "latency_ms" not in field_names(ModelCallFact)
    assert ModelCallFact.latency_ms.__class__ is property


def _a_command() -> BehaviourCommand:
    return BehaviourCommand(
        session_id="demo-01",
        command_id="command-322",
        request_id="request-0091",
        npc_id="shopkeeper-uuid",
        tier="focused",
        event_id=None,
        conversation_id=None,
        turn_id=None,
        source_sequence=1842,
        created_at_ms=1_786_208_500_984,
        expires_at_ms=1_786_208_515_000,
        dialogue="Towards the fountain!",
        action=None,
        fallback_used=False,
        command_sequence=1,
    )
