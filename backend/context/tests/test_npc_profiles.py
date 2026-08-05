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


@pytest.mark.parametrize(
    ("document", "reason"),
    [
        ({"version": 2, "profiles": [AUTHORED]}, "version"),
        ({"profiles": [AUTHORED]}, "version"),
        ({"version": 1, "profiles": [AUTHORED, AUTHORED]}, "unique"),
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
