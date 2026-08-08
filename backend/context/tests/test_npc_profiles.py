"""Owner: Jerome & Richard"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from backend.context.npc_profiles import NpcProfiles, ProfileDocumentError

SHIPPED_PROFILES = Path(__file__).resolve().parents[3] / "data" / "npc_profiles.json"

AUTHORED = {
    "npc_id": "shopkeeper-uuid",
    "name": "Mira",
    "role": "market bread seller",
    "persona": "Runs the bread stall by the fountain.",
    "speaking_style": "Warm and quick.",
    "relationships": [],
}

BY_PROFESSION: dict[str, Any] = {
    "npc_id": None,
    "profession": "farmer",
    "name": "Farmer",
    "role": "a villager who works the fields",
    "persona": "Rises before the market does.",
    "speaking_style": "Unhurried.",
    "relationships": [],
}

# What Minecraft actually mints for a villager: a world-random UUID, nothing like the placeholder
# identifiers the contract documents use in their worked examples.
LIVE_VILLAGER = "6f1b0f14-9c3a-4d2e-8b77-1e5a0c9d4432"


def write(tmp_path: Path, document: Any) -> Path:
    path = tmp_path / "npc_profiles.json"
    path.write_text(json.dumps(document))
    return path


def test_the_shipped_profile_document_loads() -> None:
    profiles = NpcProfiles.load(SHIPPED_PROFILES)

    shopkeeper = profiles.profile_for("shopkeeper-uuid")
    assert shopkeeper.authored
    assert shopkeeper.name
    assert shopkeeper.relationships


def test_an_authored_profile_keeps_its_relationships(tmp_path: Path) -> None:
    document = {
        "version": 1,
        "profiles": [
            AUTHORED | {"relationships": [{"npc_id": "guard-uuid", "relation": "relies on"}]},
            AUTHORED | {"npc_id": "guard-uuid", "name": "Bran"},
        ],
    }

    profile = NpcProfiles.load(write(tmp_path, document)).profile_for("shopkeeper-uuid")

    assert [(link.npc_id, link.relation) for link in profile.relationships] == [
        ("guard-uuid", "relies on")
    ]


def test_an_unknown_npc_receives_a_safe_generic_profile(tmp_path: Path) -> None:
    profiles = NpcProfiles.load(write(tmp_path, {"version": 1, "profiles": [AUTHORED]}))

    stranger = profiles.profile_for("nobody-uuid")

    assert stranger.authored is False
    assert stranger.npc_id == "nobody-uuid"
    assert stranger.name and stranger.role and stranger.persona and stranger.speaking_style


def test_a_profession_profile_answers_for_any_villager_holding_it(tmp_path: Path) -> None:
    """The whole point of the ticket: no identifier is agreed with Minecraft, a profession is."""
    profiles = NpcProfiles.load(write(tmp_path, {"version": 1, "profiles": [BY_PROFESSION]}))

    villager = profiles.profile_for(LIVE_VILLAGER, "Farmer")

    assert villager.authored
    assert villager.name == "Farmer"
    assert villager.npc_id == LIVE_VILLAGER


@pytest.mark.parametrize("published", ["farmer", "Farmer", "FARMER", "far mer", "far_mer"])
def test_a_profession_matches_however_minecraft_spells_it(tmp_path: Path, published: str) -> None:
    """`SnapshotBuilder.formatProfession` turns `tool_smith` into `Tool Smith` before publishing
    it, and no source fixes either spelling, so neither case nor word separators may decide a
    match."""
    profiles = NpcProfiles.load(write(tmp_path, {"version": 1, "profiles": [BY_PROFESSION]}))

    assert profiles.profile_for(LIVE_VILLAGER, published).authored


def test_a_profile_naming_this_npc_outranks_one_naming_its_profession(tmp_path: Path) -> None:
    document = {"version": 1, "profiles": [AUTHORED, BY_PROFESSION]}

    profiles = NpcProfiles.load(write(tmp_path, document))

    assert profiles.profile_for("shopkeeper-uuid", "Farmer").name == "Mira"


def test_an_unprofiled_profession_receives_the_safe_generic_profile(tmp_path: Path) -> None:
    profiles = NpcProfiles.load(write(tmp_path, {"version": 1, "profiles": [BY_PROFESSION]}))

    stranger = profiles.profile_for(LIVE_VILLAGER, "Librarian")

    assert stranger.authored is False
    assert stranger.name and stranger.role and stranger.persona and stranger.speaking_style


def test_the_shipped_document_profiles_the_professions_the_mod_publishes() -> None:
    profiles = NpcProfiles.load(SHIPPED_PROFILES)

    for profession in ("Farmer", "Librarian", "Cleric", "Toolsmith"):
        assert profiles.profile_for(LIVE_VILLAGER, profession).authored, profession


@pytest.mark.parametrize(
    ("document", "reason"),
    [
        ({"version": 2, "profiles": [AUTHORED]}, "version"),
        ({"profiles": [AUTHORED]}, "version"),
        ({"version": 1, "profiles": [AUTHORED, AUTHORED]}, "unique"),
        ({"version": 1, "profiles": [BY_PROFESSION, BY_PROFESSION]}, "unique"),
        (
            {"version": 1, "profiles": [AUTHORED | {"profession": "farmer"}]},
            "npc_id or profession",
        ),
        (
            {"version": 1, "profiles": [{k: v for k, v in BY_PROFESSION.items() if k != "profession"}]},
            "npc_id or profession",
        ),
        (
            {
                "version": 1,
                "profiles": [
                    AUTHORED | {"relationships": [{"npc_id": "farmer", "relation": "knows"}]},
                    BY_PROFESSION,
                ],
            },
            "unknown npc_id",
        ),
        (
            {
                "version": 1,
                "profiles": [
                    AUTHORED | {"relationships": [{"npc_id": "ghost", "relation": "knows"}]}
                ],
            },
            "unknown npc_id",
        ),
        ({"version": 1, "profiles": [AUTHORED | {"persona": ""}]}, "persona"),
        ({"version": 1, "profiles": [{"npc_id": "shopkeeper-uuid"}]}, "name"),
        ({"version": 1, "profiles": [AUTHORED | {"mood": "cheerful"}]}, "mood"),
    ],
)
def test_a_document_that_cannot_be_trusted_is_refused(
    tmp_path: Path, document: Any, reason: str
) -> None:
    with pytest.raises(ProfileDocumentError) as refused:
        NpcProfiles.load(write(tmp_path, document))

    assert reason in str(refused.value)


def test_a_missing_or_unreadable_document_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ProfileDocumentError):
        NpcProfiles.load(tmp_path / "absent.json")

    broken = tmp_path / "broken.json"
    broken.write_text("{not json")
    with pytest.raises(ProfileDocumentError):
        NpcProfiles.load(broken)
