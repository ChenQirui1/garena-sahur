"""Owner: Jerome & Richard

The order specification #1 fixes — NPC-and-trigger, role-and-event, tier-scripted, generic — is
tested by removing one layer at a time, so each case can only pass if the layer below it was
genuinely reached. Asserting the top layer alone would leave the other three unproven.

Those cases write their own document, which proves the resolution *logic* and nothing about the
document the service actually loads. The shipped-document cases below close that gap: a fixture
written from the same misunderstanding as the data cannot catch a key the producers never emit.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

import pytest

from backend.context.trigger_kind import TriggerKind
from backend.models.fallback import (
    GENERIC_DIALOGUE,
    SOURCE_GENERIC,
    SOURCE_NPC_AND_TRIGGER,
    SOURCE_ROLE_AND_EVENT,
    SOURCE_TIER_SCRIPTED,
    FallbackDocumentError,
    FallbackLibrary,
)
from backend.models.model_gateway import GenerationRequest
from backend.orchestration.behaviour_command import (
    MAX_DIALOGUE_CHARACTERS,
    consumer_length,
)
from backend.orchestration.router_port import AttentionTier

CHARACTERS_PER_TOKEN = 4

SHIPPED_DIALOGUE = Path(__file__).resolve().parents[3] / "data" / "cached_dialogue.json"

# The event kind every producer in this repository emits: `mock-publisher/scenario.py`,
# `backend/ingestion/tests/canonical_messages.py`, and the `game.event` example in
# `docs/message_schemas.md` §2. The shipped document has to be keyed on this exact string or the
# role-and-event rung cannot be reached in the running service.
SCENARIO_EVENT_TYPE = "market_theft"

# Tiers that reach a provider, and so can ever need fallback content. Ambient never calls a
# model, so it is not part of the ladder the shipped document has to cover.
PROVIDER_TIERS = (AttentionTier.FOCUSED, AttentionTier.REACTIVE)

NPC_LINE = "Cached for this NPC and trigger."
ROLE_LINE = "Cached for a theft witness."
FOCUSED_LINE = "Scripted focused line."
GENERIC_LINE = "Generic safe line."

FULL_DOCUMENT: dict[str, object] = {
    "version": 1,
    "by_npc_and_trigger": [
        {"npc_id": "shopkeeper-uuid", "trigger": "event", "dialogue": NPC_LINE}
    ],
    "by_role_and_event": [
        {"role": "witness", "event_type": "theft", "dialogue": ROLE_LINE},
        {"role": "nearby", "event_type": "theft", "dialogue": "Cached for a bystander."},
    ],
    "by_tier": {"focused": FOCUSED_LINE, "reactive": "Scripted reactive line."},
    "generic": GENERIC_LINE,
}


def request_for(**overrides: object) -> GenerationRequest:
    fields: dict[str, object] = {
        "request_id": "request-abc",
        "session_id": "demo-01",
        "npc_id": "shopkeeper-uuid",
        "npc_name": "Mira",
        "tier": AttentionTier.FOCUSED,
        "trigger_kind": TriggerKind.OBSERVED_EVENT,
        "conversation_id": None,
        "turn_id": None,
        "event_id": "market-theft-001",
        "source_sequence": 1842,
        "prompt": "INSTRUCTIONS",
        "trigger_text": "A theft in the market.",
        "estimated_input_tokens": 42,
        "output_token_limit": 120,
        "trigger": "event",
        "event_type": "theft",
        "roles": ("witness", "nearby"),
    }
    return GenerationRequest(**(fields | overrides))  # type: ignore[arg-type]


def library_from(document: Mapping[str, object], tmp_path: Path) -> FallbackLibrary:
    path = tmp_path / "cached_dialogue.json"
    path.write_text(json.dumps(document))
    return FallbackLibrary.load(path)


def shipped_library() -> FallbackLibrary:
    return FallbackLibrary.load(SHIPPED_DIALOGUE)


def test_the_shipped_document_answers_the_scenario_theft_for_the_robbed_shopkeeper() -> None:
    """The demo path: a provider failure on the theft's target must not fall past its cached line.

    The shipped document holds a `turn` line for this NPC and no `event` line, so an event-driven
    request misses the first rung and the role-and-event rung is the one under test.
    """
    resolved = shipped_library().resolve(
        request_for(
            npc_id="shopkeeper-uuid",
            trigger="event",
            event_type=SCENARIO_EVENT_TYPE,
            roles=("target",),
        )
    )

    assert resolved.source == SOURCE_ROLE_AND_EVENT
    assert resolved.dialogue == "My bread! Someone stop him!"


def test_the_shipped_document_resolves_every_rung_of_the_ladder() -> None:
    """Each request below matches its rung and every rung above it misses.

    Each also matches at least one rung *below* it with different text, so a rung that stopped
    resolving would be visible as the wrong line rather than as an equally passing assertion.

    The generic rung is reached through Ambient, which is not a request the pipeline makes — the
    shipped document scripts both tiers that can, which the case below asserts. Ambient is the
    only input under which the shipped document's fourth rung is observable at all, and the
    criterion is the *full* ladder: a generic line edited away should fail here, not in a demo.
    """
    library = shipped_library()

    npc_and_trigger = library.resolve(
        request_for(
            npc_id="shopkeeper-uuid",
            trigger="turn",
            event_type=SCENARIO_EVENT_TYPE,
            roles=("target",),
            tier=AttentionTier.FOCUSED,
        )
    )
    role_and_event = library.resolve(
        request_for(
            npc_id="thief-uuid",
            trigger="event",
            event_type=SCENARIO_EVENT_TYPE,
            roles=("actor",),
            tier=AttentionTier.FOCUSED,
        )
    )
    tier_scripted = library.resolve(
        request_for(
            npc_id="thief-uuid",
            trigger="event",
            event_type="market_fire",
            roles=("actor",),
            tier=AttentionTier.FOCUSED,
        )
    )
    generic = library.resolve(
        request_for(
            npc_id="thief-uuid",
            trigger="event",
            event_type="market_fire",
            roles=("actor",),
            tier=AttentionTier.AMBIENT,
        )
    )

    assert (npc_and_trigger.source, npc_and_trigger.dialogue) == (
        SOURCE_NPC_AND_TRIGGER,
        "Give me a moment, love — my head's all over the place today.",
    )
    assert (role_and_event.source, role_and_event.dialogue) == (
        SOURCE_ROLE_AND_EVENT,
        "Nothing to see. Move along.",
    )
    assert (tier_scripted.source, tier_scripted.dialogue) == (
        SOURCE_TIER_SCRIPTED,
        "Sorry — say that again? The market's got my head spinning.",
    )
    assert (generic.source, generic.dialogue) == (SOURCE_GENERIC, "...")


def test_the_shipped_document_keeps_the_generic_rung_a_last_resort() -> None:
    """Generic is unreachable in the running service only while every live tier is scripted.

    That is a property of the shipped data, not of the ladder, so it is asserted rather than
    assumed: dropping a tier line would silently demote real requests to `...`.
    """
    library = shipped_library()

    scripted = [
        library.resolve(
            request_for(
                npc_id="stranger-uuid",
                trigger="event",
                event_type="market_fire",
                roles=(),
                tier=tier,
            )
        )
        for tier in PROVIDER_TIERS
    ]

    assert [content.source for content in scripted] == [SOURCE_TIER_SCRIPTED] * len(
        PROVIDER_TIERS
    )
    assert all(content.dialogue.strip() for content in scripted)


def test_every_shipped_role_line_is_keyed_on_a_producible_event_type() -> None:
    """A role line keyed on an event kind nothing emits is dead weight in the running service."""
    entries = json.loads(SHIPPED_DIALOGUE.read_text())["by_role_and_event"]

    assert entries
    assert {entry["event_type"] for entry in entries} == {SCENARIO_EVENT_TYPE}


def test_npc_and_trigger_content_wins_over_every_other_layer(tmp_path: Path) -> None:
    resolved = library_from(FULL_DOCUMENT, tmp_path).resolve(request_for())

    assert resolved.source == SOURCE_NPC_AND_TRIGGER
    assert resolved.dialogue == NPC_LINE


def test_role_and_event_content_is_used_when_the_npc_has_none(tmp_path: Path) -> None:
    document = FULL_DOCUMENT | {"by_npc_and_trigger": []}

    resolved = library_from(document, tmp_path).resolve(request_for())

    assert resolved.source == SOURCE_ROLE_AND_EVENT
    assert resolved.dialogue == ROLE_LINE


def test_the_strongest_role_the_npc_holds_chooses_the_line(tmp_path: Path) -> None:
    """`witness` outranks `nearby`, so an NPC holding both must not get the bystander line."""
    document = FULL_DOCUMENT | {"by_npc_and_trigger": []}
    library = library_from(document, tmp_path)

    both = library.resolve(request_for(roles=("witness", "nearby")))
    only_nearby = library.resolve(request_for(roles=("nearby",)))

    assert both.dialogue == ROLE_LINE
    assert only_nearby.dialogue == "Cached for a bystander."


def test_tier_scripted_content_is_used_when_no_cache_matches(tmp_path: Path) -> None:
    document = FULL_DOCUMENT | {"by_npc_and_trigger": [], "by_role_and_event": []}

    resolved = library_from(document, tmp_path).resolve(request_for())

    assert resolved.source == SOURCE_TIER_SCRIPTED
    assert resolved.dialogue == FOCUSED_LINE


def test_the_generic_line_is_the_last_resort(tmp_path: Path) -> None:
    document = FULL_DOCUMENT | {
        "by_npc_and_trigger": [],
        "by_role_and_event": [],
        "by_tier": {},
    }

    resolved = library_from(document, tmp_path).resolve(request_for())

    assert resolved.source == SOURCE_GENERIC
    assert resolved.dialogue == GENERIC_LINE


def test_work_with_no_event_skips_the_role_and_event_layer(tmp_path: Path) -> None:
    """A conversation turn holds no event role, so role-keyed content cannot apply to it."""
    document = FULL_DOCUMENT | {"by_npc_and_trigger": []}

    resolved = library_from(document, tmp_path).resolve(
        request_for(trigger="turn", event_type=None, event_id=None, roles=())
    )

    assert resolved.source == SOURCE_TIER_SCRIPTED


def test_an_empty_library_still_answers_every_request() -> None:
    resolved = FallbackLibrary.empty().resolve(request_for())

    assert resolved.source == SOURCE_GENERIC
    assert resolved.dialogue == GENERIC_DIALOGUE


def test_fallback_behaviour_is_marked_as_fallback_and_carries_no_action(
    tmp_path: Path,
) -> None:
    """Issue #4 owns the action vocabulary, so the generic layer speaks rather than acting."""
    library = library_from(FULL_DOCUMENT | {"by_npc_and_trigger": []}, tmp_path)

    behaviour = library.behaviour(request_for(), CHARACTERS_PER_TOKEN)

    assert behaviour.fallback_used is True
    assert behaviour.action is None
    assert behaviour.dialogue == ROLE_LINE
    assert behaviour.output_tokens > 0


def test_an_unreadable_document_is_refused_rather_than_half_loaded(tmp_path: Path) -> None:
    path = tmp_path / "cached_dialogue.json"
    path.write_text("{not json")

    with pytest.raises(FallbackDocumentError):
        FallbackLibrary.load(path)


def test_a_cache_entry_missing_its_key_is_refused(tmp_path: Path) -> None:
    document: Mapping[str, object] = {
        "by_npc_and_trigger": [{"npc_id": "shopkeeper-uuid", "dialogue": "no trigger"}]
    }

    with pytest.raises(FallbackDocumentError):
        library_from(document, tmp_path)


def test_an_over_long_authored_line_is_bounded_before_it_becomes_a_command() -> None:
    """Fallback content never passes through the gateway, so it carries its own bound.

    Without one, an authored line past 512 characters reaches Minecraft, is dropped, and the NPC
    stays silent — in the one path that exists to keep it talking when generation failed.
    """
    library = FallbackLibrary({}, {}, {}, "g" * (MAX_DIALOGUE_CHARACTERS + 100))

    behaviour = library.behaviour(request_for(), CHARACTERS_PER_TOKEN)

    assert behaviour.dialogue is not None
    assert consumer_length(behaviour.dialogue) == MAX_DIALOGUE_CHARACTERS


def test_the_reported_output_tokens_describe_the_bounded_line_not_the_authored_one() -> None:
    """Telemetry counts what was published. Estimating from the authored line would report
    tokens for text no NPC ever said."""
    library = FallbackLibrary({}, {}, {}, "g" * (MAX_DIALOGUE_CHARACTERS + 100))

    behaviour = library.behaviour(request_for(), CHARACTERS_PER_TOKEN)

    assert behaviour.output_tokens == MAX_DIALOGUE_CHARACTERS // CHARACTERS_PER_TOKEN


def test_an_authored_line_inside_the_limit_is_left_exactly_as_written() -> None:
    library = FallbackLibrary({}, {}, {}, GENERIC_DIALOGUE)

    behaviour = library.behaviour(request_for(), CHARACTERS_PER_TOKEN)

    assert behaviour.dialogue == GENERIC_DIALOGUE
