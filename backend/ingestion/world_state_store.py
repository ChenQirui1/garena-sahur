"""Store only the latest world state; overwrite obsolete snapshots.

Owner: Jerome & Richard
"""

from __future__ import annotations

from backend.ingestion.message_validation import WorldSnapshot


class WorldStateStore:
    """Latest-value-wins world state, ordered per session and world."""

    def __init__(self) -> None:
        self._latest: dict[tuple[str, str], WorldSnapshot] = {}
        self._applied_by_session: dict[str, WorldSnapshot] = {}

    def apply_if_newer(self, snapshot: WorldSnapshot) -> bool:
        """Replace retained state when ``snapshot`` is newer; never regress it."""
        key = (snapshot.session_id, snapshot.world_id)
        retained = self._latest.get(key)
        if retained is not None and snapshot.sequence <= retained.sequence:
            return False
        self._latest[key] = snapshot
        self._applied_by_session[snapshot.session_id] = snapshot
        return True

    def latest(self, session_id: str, world_id: str) -> WorldSnapshot | None:
        return self._latest.get((session_id, world_id))

    def latest_for_session(self, session_id: str) -> WorldSnapshot | None:
        """The most recently applied state in a session, for work that names no world.

        Sequences are per session *and* world, so they cannot be compared across worlds;
        arrival order is the only ordering that holds when a session spans more than one.
        """
        return self._applied_by_session.get(session_id)

    def forget(self, session_id: str) -> None:
        """Drop one session's retained state, so a cleaned session starts from nothing.

        Without this a cleaned session would keep its last sequence and reject the snapshot
        that restarts it as stale.
        """
        self._applied_by_session.pop(session_id, None)
        for key in [key for key in self._latest if key[0] == session_id]:
            self._latest.pop(key, None)
