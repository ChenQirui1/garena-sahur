"""Load NPC names, roles, personalities and relationships.

Owner: Jerome & Richard

Profiles come from the backend-owned local document, never from a published `npc.profile`
record. A document that cannot be trusted fails readiness rather than the process, and one
missing persona degrades to a safe generic character instead of ending the demo.

A profile is matched either to one named NPC or to a profession. Identity is the weaker key in
practice: Minecraft mints a world-random UUID per villager per world, so a document can only
name one that some other artifact also fixes. Profession is observed on every villager the mod
publishes, which is why it is the key the live game resolves on.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path

from pydantic import BaseModel, ConfigDict, StringConstraints, ValidationError, model_validator
from typing import Annotated

SUPPORTED_VERSION = 1

GENERIC_NAME = "Villager"
GENERIC_ROLE = "market villager"
GENERIC_PERSONA = "An ordinary resident of the market who keeps to their own business."
GENERIC_SPEAKING_STYLE = "Plain and brief."

Authored = Annotated[str, StringConstraints(min_length=1)]


class ProfileDocumentError(ValueError):
    """The local profile document cannot be trusted, so the service is not ready."""


@dataclass(frozen=True, slots=True)
class Relationship:
    npc_id: str
    relation: str


@dataclass(frozen=True, slots=True)
class NpcProfile:
    # `None` while a profession persona describes no particular villager. Every profile
    # `profile_for` hands out names the NPC it was resolved for.
    npc_id: str | None
    name: str
    role: str
    persona: str
    speaking_style: str
    relationships: tuple[Relationship, ...]
    authored: bool
    profession: str | None = None


def profession_key(profession: str) -> str:
    """The spelling-insensitive form a profession is matched on.

    Minecraft's registry name is `tool_smith`; the mod publishes what
    `SnapshotBuilder.formatProfession` makes of it, `Tool Smith`. No source fixes either
    spelling, so neither case nor word separators may decide whether a persona is found.
    """
    return "".join(profession.replace("_", " ").split()).casefold()


class _Relationship(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    npc_id: Authored
    relation: Authored


class _Profile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    npc_id: Authored | None = None
    profession: Authored | None = None
    name: Authored
    role: Authored
    persona: Authored
    speaking_style: Authored
    relationships: list[_Relationship] = []

    @model_validator(mode="after")
    def check_exactly_one_key(self) -> _Profile:
        if (self.npc_id is None) == (self.profession is None):
            raise ValueError(
                f"{self.name} must carry exactly one of npc_id or profession, not both or neither"
            )
        return self


class _Document(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int
    profiles: list[_Profile]
    owner: str | None = None

    @model_validator(mode="after")
    def check_profiles_resolve(self) -> _Document:
        if self.version != SUPPORTED_VERSION:
            raise ValueError(f"unsupported profile document version {self.version}")

        npc_ids = [profile.npc_id for profile in self.profiles if profile.npc_id is not None]
        if len(set(npc_ids)) != len(npc_ids):
            raise ValueError("profiles must have unique npc_id values")

        professions = [
            profession_key(profile.profession)
            for profile in self.profiles
            if profile.profession is not None
        ]
        if len(set(professions)) != len(professions):
            raise ValueError("profiles must have unique profession values")

        # Relationships stay identity-to-identity, deliberately: a profession names a
        # population rather than a participant, so "wary of the thief" authored against one
        # would assert a stance towards every villager holding it, in every session.
        known = set(npc_ids)
        for profile in self.profiles:
            for relationship in profile.relationships:
                if relationship.npc_id not in known:
                    raise ValueError(
                        f"{profile.name} references unknown npc_id {relationship.npc_id}"
                    )
        return self


class NpcProfiles:
    """The authored cast, with a safe stand-in for anyone the document does not name."""

    def __init__(
        self, profiles: dict[str, NpcProfile], by_profession: dict[str, NpcProfile]
    ) -> None:
        self._profiles = profiles
        self._by_profession = by_profession

    @classmethod
    def empty(cls) -> NpcProfiles:
        return cls({}, {})

    @classmethod
    def load(cls, path: Path) -> NpcProfiles:
        try:
            document = _Document.model_validate(json.loads(path.read_text()))
        except (OSError, json.JSONDecodeError, ValidationError) as unusable:
            raise ProfileDocumentError(f"{path}: {unusable}") from unusable

        authored = [_authored(profile) for profile in document.profiles]
        return cls(
            {
                profile.npc_id: profile
                for profile in authored
                if profile.npc_id is not None
            },
            {
                profession_key(profile.profession): profile
                for profile in authored
                if profile.profession is not None
            },
        )

    def profile_for(self, npc_id: str, profession: str | None = None) -> NpcProfile:
        """The persona for one observed NPC: its own if it has one, its profession's otherwise.

        Identity wins because a profile naming this NPC was written about this NPC, while a
        profession profile was written about everyone holding it.
        """
        named = self._profiles.get(npc_id)
        if named is not None:
            return named

        by_profession = (
            self._by_profession.get(profession_key(profession)) if profession else None
        )
        if by_profession is not None:
            return replace(by_profession, npc_id=npc_id)

        return NpcProfile(
            npc_id=npc_id,
            name=GENERIC_NAME,
            role=GENERIC_ROLE,
            persona=GENERIC_PERSONA,
            speaking_style=GENERIC_SPEAKING_STYLE,
            relationships=(),
            authored=False,
            profession=profession,
        )


def _authored(profile: _Profile) -> NpcProfile:
    return NpcProfile(
        npc_id=profile.npc_id,
        name=profile.name,
        role=profile.role,
        persona=profile.persona,
        speaking_style=profile.speaking_style,
        relationships=tuple(
            Relationship(link.npc_id, link.relation) for link in profile.relationships
        ),
        authored=True,
        profession=profile.profession,
    )
