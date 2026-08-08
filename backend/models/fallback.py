"""What an NPC says when the provider did not answer.

Owner: Jerome & Richard

Specification #1 fixes the order: cached content for this NPC and trigger, then cached content
for this role and event, then something scripted for the tier, then a generic safe line. Each
layer is narrower than the one below it, so the first hit is the most specific thing the backend
knows how to say.

Two things are deliberately not here. Nothing retries — the attempt that failed is spent, and
this only decides what to publish instead. And nothing emits an `action`: specification #1 calls
the last layer a "generic safe action", but the executable action vocabulary is Ivan's under
coordination issue #4, so the generic layer speaks instead of inventing a verb Minecraft cannot
apply. The command still carries executable meaning, because `docs/message_schemas.md` §6 is
satisfied by dialogue alone.

The document's shape is not described by any source. It is backend-local configuration that
reaches no wire, so it is defined here rather than negotiated.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from backend.models.model_gateway import GeneratedBehaviour, GenerationRequest
from backend.models.token_estimate import estimate_tokens
from backend.orchestration.behaviour_command import bounded_dialogue

PROVIDER = "fallback"

SOURCE_NPC_AND_TRIGGER = "npc_and_trigger"
SOURCE_ROLE_AND_EVENT = "role_and_event"
SOURCE_TIER_SCRIPTED = "tier_scripted"
SOURCE_GENERIC = "generic"

# The order is specification #1's, most specific first. It is data rather than a chain of `if`s
# so that "what order does fallback resolve in" is answerable by reading one list.
RESOLUTION_ORDER = (
    SOURCE_NPC_AND_TRIGGER,
    SOURCE_ROLE_AND_EVENT,
    SOURCE_TIER_SCRIPTED,
    SOURCE_GENERIC,
)

# The last resort has to exist without configuration, or a missing document would leave an
# accepted trigger with nothing publishable and the conversation waiting for ever.
GENERIC_DIALOGUE = "..."


class FallbackDocumentError(ValueError):
    """The cached-dialogue document could not be read, so fallback content is unusable."""


@dataclass(frozen=True, slots=True)
class FallbackContent:
    dialogue: str
    source: str


class FallbackLibrary:
    """Scripted and cached dialogue, resolved in the specified precedence."""

    def __init__(
        self,
        by_npc_and_trigger: Mapping[tuple[str, str], str],
        by_role_and_event: Mapping[tuple[str, str], str],
        by_tier: Mapping[str, str],
        generic: str,
    ) -> None:
        self._by_npc_and_trigger = by_npc_and_trigger
        self._by_role_and_event = by_role_and_event
        self._by_tier = by_tier
        self._generic = generic

    @classmethod
    def load(cls, path: Path) -> "FallbackLibrary":
        try:
            document = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as unreadable:
            raise FallbackDocumentError(f"{path} could not be read: {unreadable}") from None
        if not isinstance(document, dict):
            raise FallbackDocumentError(f"{path} must contain an object")
        return cls(
            by_npc_and_trigger=_keyed(document, "by_npc_and_trigger", "npc_id", "trigger"),
            by_role_and_event=_keyed(document, "by_role_and_event", "role", "event_type"),
            by_tier=_strings(document.get("by_tier", {}), "by_tier"),
            generic=_generic(document),
        )

    @classmethod
    def empty(cls) -> "FallbackLibrary":
        """No cached or scripted content, so everything resolves to the generic line."""
        return cls({}, {}, {}, GENERIC_DIALOGUE)

    def resolve(self, request: GenerationRequest) -> FallbackContent:
        """The most specific content this request can have, never `None`."""
        for source in RESOLUTION_ORDER:
            dialogue = self._from(source, request)
            if dialogue is not None:
                return FallbackContent(dialogue=dialogue, source=source)
        raise AssertionError("the generic layer always resolves")

    def behaviour(
        self, request: GenerationRequest, characters_per_token: int
    ) -> GeneratedBehaviour:
        """The resolved content as the same normalised result a provider would have returned.

        Bounded here as well as in the gateway because fallback content never passes through it:
        an over-long authored line would otherwise reach Minecraft and be dropped, leaving the
        NPC silent in exactly the case fallback exists to keep it talking.
        """
        content = self.resolve(request)
        dialogue = bounded_dialogue(content.dialogue)
        return GeneratedBehaviour(
            dialogue=dialogue,
            action=None,
            provider=PROVIDER,
            model=content.source,
            input_tokens=request.estimated_input_tokens,
            output_tokens=estimate_tokens(dialogue, characters_per_token),
            fallback_used=True,
        )

    def _from(self, source: str, request: GenerationRequest) -> str | None:
        if source == SOURCE_NPC_AND_TRIGGER:
            return self._by_npc_and_trigger.get((request.npc_id, request.trigger))
        if source == SOURCE_ROLE_AND_EVENT:
            return self._role_and_event(request)
        if source == SOURCE_TIER_SCRIPTED:
            return self._by_tier.get(request.tier.value)
        return self._generic

    def _role_and_event(self, request: GenerationRequest) -> str | None:
        """Roles are already in relevance order, so the strongest part the NPC holds wins."""
        if request.event_type is None:
            return None
        for role in request.roles:
            dialogue = self._by_role_and_event.get((role, request.event_type))
            if dialogue is not None:
                return dialogue
        return None


def _keyed(
    document: Mapping[str, Any], field: str, first: str, second: str
) -> dict[tuple[str, str], str]:
    entries = document.get(field, [])
    if not isinstance(entries, list):
        raise FallbackDocumentError(f"{field} must be a list")
    keyed: dict[tuple[str, str], str] = {}
    for entry in entries:
        if not isinstance(entry, dict) or not {first, second, "dialogue"} <= entry.keys():
            raise FallbackDocumentError(
                f"each {field} entry needs {first}, {second} and dialogue"
            )
        keyed[(str(entry[first]), str(entry[second]))] = str(entry["dialogue"])
    return keyed


def _strings(value: object, field: str) -> dict[str, str]:
    if not isinstance(value, dict):
        raise FallbackDocumentError(f"{field} must be an object")
    return {str(key): str(text) for key, text in value.items()}


def _generic(document: Mapping[str, Any]) -> str:
    generic = document.get("generic", GENERIC_DIALOGUE)
    if not isinstance(generic, str) or not generic.strip():
        raise FallbackDocumentError("generic must be non-empty text")
    return generic
