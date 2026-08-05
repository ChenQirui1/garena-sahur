"""The instruction Minecraft applies while it is still current.

Owner: Jerome & Richard

Field names and nesting are the team's documented `behaviour_command`. `command_sequence` is
the one addition: specification #1 proposes it so several commands from one source snapshot can
be ordered per NPC, and coordination issue #4 has not accepted it yet, so it is emitted last and
labelled provisional rather than folded in as though it were agreed.

Minecraft decides whether to apply a command. The backend only guarantees that every command it
publishes is fresh, ordered, expiring, and carries something executable.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256

SCHEMA_VERSION = "1.0"
MESSAGE_TYPE = "behaviour_command"


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
