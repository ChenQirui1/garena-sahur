"""Load NPC names, roles, personalities and relationships.

Owner: Jerome & Richard

Profiles come from the backend-owned local document, never from a published `npc.profile`
record. A document that cannot be trusted fails readiness rather than the process, and one
missing persona degrades to a safe generic character instead of ending the demo.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
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
    npc_id: str
    name: str
    role: str
    persona: str
    speaking_style: str
    relationships: tuple[Relationship, ...]
    authored: bool


class _Relationship(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    npc_id: Authored
    relation: Authored


class _Profile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    npc_id: Authored
    name: Authored
    role: Authored
    persona: Authored
    speaking_style: Authored
    relationships: list[_Relationship] = []


class _Document(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int
    profiles: list[_Profile]
    owner: str | None = None

    @model_validator(mode="after")
    def check_profiles_resolve(self) -> _Document:
        if self.version != SUPPORTED_VERSION:
            raise ValueError(f"unsupported profile document version {self.version}")

        npc_ids = [profile.npc_id for profile in self.profiles]
        if len(set(npc_ids)) != len(npc_ids):
            raise ValueError("profiles must have unique npc_id values")

        known = set(npc_ids)
        for profile in self.profiles:
            for relationship in profile.relationships:
                if relationship.npc_id not in known:
                    raise ValueError(
                        f"{profile.npc_id} references unknown npc_id {relationship.npc_id}"
                    )
        return self


class NpcProfiles:
    """The authored cast, with a safe stand-in for anyone the document does not name."""

    def __init__(self, profiles: dict[str, NpcProfile]) -> None:
        self._profiles = profiles

    @classmethod
    def empty(cls) -> NpcProfiles:
        return cls({})

    @classmethod
    def load(cls, path: Path) -> NpcProfiles:
        try:
            document = _Document.model_validate(json.loads(path.read_text()))
        except (OSError, json.JSONDecodeError, ValidationError) as unusable:
            raise ProfileDocumentError(f"{path}: {unusable}") from unusable

        return cls(
            {
                profile.npc_id: NpcProfile(
                    npc_id=profile.npc_id,
                    name=profile.name,
                    role=profile.role,
                    persona=profile.persona,
                    speaking_style=profile.speaking_style,
                    relationships=tuple(
                        Relationship(link.npc_id, link.relation)
                        for link in profile.relationships
                    ),
                    authored=True,
                )
                for profile in document.profiles
            }
        )

    def profile_for(self, npc_id: str) -> NpcProfile:
        authored = self._profiles.get(npc_id)
        if authored is not None:
            return authored
        return NpcProfile(
            npc_id=npc_id,
            name=GENERIC_NAME,
            role=GENERIC_ROLE,
            persona=GENERIC_PERSONA,
            speaking_style=GENERIC_SPEAKING_STYLE,
            relationships=(),
            authored=False,
        )
