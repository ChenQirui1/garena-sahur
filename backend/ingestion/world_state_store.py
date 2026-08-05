"""Store only the latest world state; overwrite obsolete snapshots.

Owner: Jerome & Richard
"""

from __future__ import annotations

from backend.ingestion.message_validation import WorldSnapshot


class StorageUnavailable(RuntimeError):
    """State could not be read or written, so the message must not be acknowledged."""


class WorldStateStore:
    """Latest-value-wins world state, ordered per session and world."""

    def __init__(self) -> None:
        self._latest: dict[tuple[str, str], WorldSnapshot] = {}

    def apply_if_newer(self, snapshot: WorldSnapshot) -> bool:
        """Replace retained state when ``snapshot`` is newer; never regress it."""
        key = (snapshot.session_id, snapshot.world_id)
        retained = self._latest.get(key)
        if retained is not None and snapshot.sequence <= retained.sequence:
            return False
        self._latest[key] = snapshot
        return True

    def latest(self, session_id: str, world_id: str) -> WorldSnapshot | None:
        return self._latest.get((session_id, world_id))
