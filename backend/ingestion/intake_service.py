"""The canonical intake boundary every transport adapter converges on.

Owner: Jerome & Richard
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from backend.ingestion.message_validation import (
    TOPIC_LEGACY_NPC_PROFILE,
    TOPIC_WORLD_SNAPSHOT,
    MessageValidationError,
    validate_world_snapshot,
)
from backend.ingestion.world_state_store import StorageUnavailable, WorldStateStore
from backend.orchestration.router_handoff import RouterHandoff
from backend.orchestration.routing_snapshot import build_routing_snapshot

IGNORED_LEGACY_PROFILE_DETAIL = (
    f"{TOPIC_LEGACY_NPC_PROFILE} is accepted for compatibility and ignored; "
    "profiles are loaded from the backend-owned local document"
)


class IntakeOutcome(StrEnum):
    APPLIED = "applied"
    STALE = "stale"
    IGNORED = "ignored"
    INVALID = "invalid"
    UNKNOWN_TOPIC = "unknown_topic"
    STORAGE_UNAVAILABLE = "storage_unavailable"


@dataclass(frozen=True, slots=True)
class IntakeResult:
    outcome: IntakeOutcome
    detail: str | None = None


class IntakeService:
    """Validate a canonical message, update owned state, and hand routing work off."""

    def __init__(
        self,
        store: WorldStateStore,
        handoff: RouterHandoff,
        max_snapshot_candidates: int,
    ) -> None:
        self._store = store
        self._handoff = handoff
        self._max_snapshot_candidates = max_snapshot_candidates

    def submit(self, topic: str, message: object) -> IntakeResult:
        if topic == TOPIC_LEGACY_NPC_PROFILE:
            return IntakeResult(IntakeOutcome.IGNORED, IGNORED_LEGACY_PROFILE_DETAIL)
        if topic != TOPIC_WORLD_SNAPSHOT:
            return IntakeResult(IntakeOutcome.UNKNOWN_TOPIC, f"unknown topic: {topic!r}")

        try:
            snapshot = validate_world_snapshot(message)
        except MessageValidationError as invalid:
            return IntakeResult(IntakeOutcome.INVALID, str(invalid))

        if len(snapshot.candidates) > self._max_snapshot_candidates:
            return IntakeResult(
                IntakeOutcome.INVALID,
                f"candidates: at most {self._max_snapshot_candidates} candidates per snapshot",
            )

        try:
            applied = self._store.apply_if_newer(snapshot)
        except StorageUnavailable as unavailable:
            return IntakeResult(IntakeOutcome.STORAGE_UNAVAILABLE, str(unavailable))

        if not applied:
            return IntakeResult(
                IntakeOutcome.STALE,
                f"sequence {snapshot.sequence} does not supersede retained state",
            )

        self._handoff.submit(build_routing_snapshot(snapshot))
        return IntakeResult(IntakeOutcome.APPLIED)
