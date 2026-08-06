"""What the active-trigger section of a built context holds.

Owner: Jerome & Richard

It sits alone rather than inside `context_builder` because the provider interface and the
prompt renderers need it without needing profiles, stores, or the builder itself.
"""

from __future__ import annotations

from enum import StrEnum


class TriggerKind(StrEnum):
    """Player speech, or something the NPC observed.

    This is not the generation trigger. That says *why* an NPC speaks, and promotion and expiry
    reuse whichever context path still requires foreground behaviour — so only the builder
    entry point that produced a context can say which of the two its trigger section is.
    """

    PLAYER_SPEECH = "player_speech"
    OBSERVED_EVENT = "observed_event"
