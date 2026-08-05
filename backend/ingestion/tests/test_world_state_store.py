"""Owner: Jerome & Richard"""

from __future__ import annotations

from backend.ingestion.message_validation import validate_world_snapshot
from backend.ingestion.tests.canonical_messages import SESSION_ID, WORLD_ID, world_snapshot
from backend.ingestion.world_state_store import WorldStateStore


def test_a_newer_snapshot_replaces_older_state() -> None:
    store = WorldStateStore()

    assert store.apply_if_newer(validate_world_snapshot(world_snapshot(sequence=1))) is True
    assert store.apply_if_newer(validate_world_snapshot(world_snapshot(sequence=2))) is True

    retained = store.latest(SESSION_ID, WORLD_ID)
    assert retained is not None
    assert retained.sequence == 2


def test_identical_and_stale_snapshots_do_not_regress_state() -> None:
    store = WorldStateStore()
    store.apply_if_newer(validate_world_snapshot(world_snapshot(sequence=7)))

    assert store.apply_if_newer(validate_world_snapshot(world_snapshot(sequence=7))) is False
    assert store.apply_if_newer(validate_world_snapshot(world_snapshot(sequence=3))) is False

    retained = store.latest(SESSION_ID, WORLD_ID)
    assert retained is not None
    assert retained.sequence == 7


def test_ordering_is_kept_per_session_and_world() -> None:
    store = WorldStateStore()
    store.apply_if_newer(validate_world_snapshot(world_snapshot(sequence=9)))

    other_world = validate_world_snapshot(world_snapshot(sequence=1, world_id="nether"))
    assert store.apply_if_newer(other_world) is True

    other_session = validate_world_snapshot(world_snapshot(sequence=1, session_id="demo-02"))
    assert store.apply_if_newer(other_session) is True

    assert store.latest(SESSION_ID, WORLD_ID).sequence == 9
    assert store.latest(SESSION_ID, "nether").sequence == 1
    assert store.latest("demo-02", WORLD_ID).sequence == 1


def test_an_unseen_session_has_no_retained_state() -> None:
    assert WorldStateStore().latest(SESSION_ID, WORLD_ID) is None
