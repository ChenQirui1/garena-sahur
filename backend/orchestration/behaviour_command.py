"""The instruction Minecraft applies while it is still current.

Owner: Jerome & Richard

Field names and nesting are the team's documented `behaviour_command`. `command_sequence` is
the one addition: specification #1 proposes it so several commands from one source snapshot can
be ordered per NPC, and coordination issue #4 has not accepted it yet, so it is emitted last and
labelled provisional rather than folded in as though it were agreed.

Minecraft decides whether to apply a command. The backend only guarantees that every command it
publishes is fresh, ordered, expiring, carries something executable, and sits inside the limits
the consumer will read.

Those limits are Ivan's, confirmed on coordination issue #4 on 2026-08-07 and verified against
`SpotlightConfig.java` and `BehaviourCommand.parse` in the shipped mod. `docs/message_schemas.md`
§6 records none of them — its only limit-like statement is § Common conventions' "IDs are stable,
non-empty strings" — so they are named here rather than derived from the document. Issue #60
proposes recording them in §6 with the team; until it does, a command satisfying every rule §6
writes down can still be refused on arrival, which is what the constants below exist to prevent.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256

SCHEMA_VERSION = "1.0"
MESSAGE_TYPE = "behaviour_command"

MAX_IDENTIFIER_CHARACTERS = 128
MAX_DIALOGUE_CHARACTERS = 512
MAX_PAYLOAD_BYTES = 65_536

# `BehaviourCommand.parse`'s own split: the first five are required and rejected when empty, the
# last three are nullable and bounded only when present. `tier` is not an identifier but the
# consumer bounds it with the same constant, so it is checked with them rather than alone.
REQUIRED_TEXT_FIELDS = ("session_id", "command_id", "request_id", "npc_id", "tier")
NULLABLE_TEXT_FIELDS = ("event_id", "conversation_id", "turn_id")


class CommandFieldOutOfBounds(ValueError):
    """A command field the consumer would refuse, caught where the command is built."""


class PayloadTooLarge(ValueError):
    """A serialized command larger than the consumer will read off the transport."""


def consumer_length(text: str) -> int:
    """The length the consumer measures, which is not the length Python measures.

    Java's `String.length()` counts UTF-16 code units; Python counts code points. A line of
    astral characters is half as long here as it is to the mod, so measuring the way the consumer
    measures is the only way our bound is actually its bound.
    """
    return len(text.encode("utf-16-le")) // 2


@dataclass(frozen=True, slots=True)
class BehaviourCommand:
    session_id: str
    command_id: str
    request_id: str
    npc_id: str
    tier: str
    event_id: str | None
    conversation_id: str | None
    turn_id: str | None
    source_sequence: int
    created_at_ms: int
    expires_at_ms: int
    dialogue: str | None
    action: str | None
    fallback_used: bool
    command_sequence: int

    def __post_init__(self) -> None:
        """Refuse to exist rather than be built and refused on arrival.

        Checked here because every path that produces a command — generation, fallback, and the
        rows restart recovery reads back — passes through construction, and none of them should
        have to remember.
        """
        for field in REQUIRED_TEXT_FIELDS:
            _check_text(field, getattr(self, field), nullable=False)
        for field in NULLABLE_TEXT_FIELDS:
            _check_text(field, getattr(self, field), nullable=True)
        if self.dialogue is not None and not (
            1 <= consumer_length(self.dialogue) <= MAX_DIALOGUE_CHARACTERS
        ):
            raise CommandFieldOutOfBounds(
                f"dialogue must be null or 1 to {MAX_DIALOGUE_CHARACTERS} characters,"
                f" not {consumer_length(self.dialogue)}"
            )

    def as_payload(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "message_type": MESSAGE_TYPE,
            "session_id": self.session_id,
            "command_id": self.command_id,
            "request_id": self.request_id,
            "npc_id": self.npc_id,
            "tier": self.tier,
            "event_id": self.event_id,
            "conversation_id": self.conversation_id,
            "turn_id": self.turn_id,
            "source_sequence": self.source_sequence,
            "created_at_ms": self.created_at_ms,
            "expires_at_ms": self.expires_at_ms,
            "dialogue": self.dialogue,
            "action": self.action,
            "fallback_used": self.fallback_used,
            "command_sequence": self.command_sequence,
        }


def identity_digest(*parts: str | None) -> str:
    """A stable short identity, so a retry of the same work reuses the same identifiers."""
    material = "".join(part or "" for part in parts)
    return sha256(material.encode()).hexdigest()[:12]


def bounded_dialogue(text: str) -> str:
    """Generated text cut to what the consumer accepts, at a whole character.

    Cutting at the limit could hand the mod half a surrogate pair, so the walk stops before a
    character that will not fit rather than slicing the string.
    """
    if consumer_length(text) <= MAX_DIALOGUE_CHARACTERS:
        return text

    kept: list[str] = []
    remaining = MAX_DIALOGUE_CHARACTERS
    for character in text:
        width = consumer_length(character)
        if width > remaining:
            break
        kept.append(character)
        remaining -= width
    return "".join(kept)


def serialized_payload(command: BehaviourCommand) -> str:
    """The exact bytes every publication attempt sends, or nothing at all.

    The size is checked where the bytes first exist rather than at publication, so an oversized
    command never reaches durable storage and cannot be found and republished by a later restart.
    """
    payload = json.dumps(command.as_payload())
    size = len(payload.encode())
    if size > MAX_PAYLOAD_BYTES:
        raise PayloadTooLarge(
            f"{size} bytes exceeds the {MAX_PAYLOAD_BYTES} byte maximum the consumer reads"
        )
    return payload


def _check_text(field: str, value: str | None, nullable: bool) -> None:
    if value is None:
        if not nullable:
            raise CommandFieldOutOfBounds(f"{field} must be present")
        return
    if not value:
        raise CommandFieldOutOfBounds(f"{field} must not be empty")
    if consumer_length(value) > MAX_IDENTIFIER_CHARACTERS:
        raise CommandFieldOutOfBounds(f"{field} exceeds {MAX_IDENTIFIER_CHARACTERS} characters")
