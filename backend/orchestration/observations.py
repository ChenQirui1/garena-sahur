"""Orchestration decisions the backend must be able to show without a telemetry contract.

Owner: Jerome & Richard

Coordination issue #3 has accepted no orchestration fact beyond `model_call`, so nothing here
crosses the telemetry port to Elson & Daniel. These stay in-process and bounded: they exist so
a suppressed generation or a missing profile is observable rather than silent.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from typing import Mapping

logger = logging.getLogger(__name__)

RETAINED = 256

MISSING_PROFILE = "missing_profile"
GENERATION_SUPPRESSED = "generation_suppressed"
EVENT_GENERATION_SUPPRESSED = "event_generation_suppressed"
UNCONFIRMED_TURN_DISCARDED = "unconfirmed_turn_discarded"
NO_WORLD_STATE = "no_world_state"
ROUTING_NOT_REFRESHED = "routing_not_refreshed"
MODEL_CALL_FAILED = "model_call_failed"
COMMAND_NOT_PUBLISHED = "command_not_published"


@dataclass(frozen=True, slots=True)
class Observation:
    name: str
    fields: Mapping[str, object]


class Observations:
    def __init__(self) -> None:
        self._recorded: deque[Observation] = deque(maxlen=RETAINED)

    @property
    def recorded(self) -> tuple[Observation, ...]:
        return tuple(self._recorded)

    def note(self, name: str, **fields: object) -> None:
        logger.info("%s %s", name, fields)
        self._recorded.append(Observation(name, dict(fields)))
