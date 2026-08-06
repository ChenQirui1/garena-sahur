"""Owner: Jerome & Richard

The order specification #1 fixes — NPC-and-trigger, role-and-event, tier-scripted, generic — is
tested by removing one layer at a time, so each case can only pass if the layer below it was
genuinely reached. Asserting the top layer alone would leave the other three unproven.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

import pytest

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
from backend.orchestration.router_port import AttentionTier

CHARACTERS_PER_TOKEN = 4

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
