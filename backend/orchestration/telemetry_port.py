"""The narrow boundary the per-attempt model-call fact leaves the backend through.

Owner: Jerome & Richard

`backend/telemetry/` is Elson & Daniel's, so the port lives here and the aggregation behind it
stays theirs. `model_call` is the one fact the team has documented; the record below uses its
field names verbatim so an aggregator can read it without translation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "1.0"
RECORD_TYPE = "model_call"

STATUS_SUCCESS = "success"
STATUS_ERROR = "error"


@dataclass(frozen=True, slots=True)
class ModelCallFact:
    """One attempted provider call, whatever its outcome."""

    session_id: str
    request_id: str
    npc_id: str
    tier: str
    # `null` when the attempt failed before any provider identified itself, per
    # `docs/message_schemas.md:397`.
    provider: str | None
    model: str | None
    event_id: str | None
    conversation_id: str | None
    turn_id: str | None
    source_sequence: int
    started_at_ms: int
    completed_at_ms: int
    input_tokens: int
    output_tokens: int
    status: str
    fallback_used: bool
    error_code: str | None

    @property
    def latency_ms(self) -> int:
        return self.completed_at_ms - self.started_at_ms

    def as_record(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "record_type": RECORD_TYPE,
            "session_id": self.session_id,
            "request_id": self.request_id,
            "npc_id": self.npc_id,
            "tier": self.tier,
            "provider": self.provider,
            "model": self.model,
            "event_id": self.event_id,
            "conversation_id": self.conversation_id,
            "turn_id": self.turn_id,
            "source_sequence": self.source_sequence,
            "started_at_ms": self.started_at_ms,
            "completed_at_ms": self.completed_at_ms,
            "latency_ms": self.latency_ms,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "status": self.status,
            "fallback_used": self.fallback_used,
            "error_code": self.error_code,
        }


class TelemetryPort(Protocol):
    def record_model_call(self, fact: ModelCallFact) -> None: ...


class LoggingTelemetry:
    """The development sink, until issue #10 supplies the real telemetry implementation."""

    def record_model_call(self, fact: ModelCallFact) -> None:
        logger.info("model_call %s", fact.as_record())
