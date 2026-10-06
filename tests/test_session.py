from datetime import datetime, timedelta, timezone
import pytest
from studyqueues.session import Session, daily_seconds
from studyqueues.models import Completion
from studyqueues.state_store import StateStore, StateError


class Clock:
    def __init__(self, wall=None):
        self.value = 0.0
        self.wall = wall or datetime(2026, 10, 4, 12, tzinfo=timezone(timedelta(hours=3)))

    def step(self, seconds):
        self.value += seconds
        self.wall += timedelta(seconds=seconds)

    def session(self, queue="university"):
        return Session(queue, "queue.md", 1, lambda: self.value, lambda: self.wall)


def test_pause_resume_overtime_extend_and_finish():
    clock = Clock()
    session = clock.session()
    for _ in range(6):
        clock.step(10)
        session.tick()
    assert session.remaining == 0 and session.running
    clock.step(5)
    session.pause()
    clock.step(600)
    session.tick()
    assert session.active_seconds == 65 and session.remaining == -5
    session.extend(10)
    assert session.remaining == 595
    session.resume()
    clock.step(3)
    session.finish()
    assert session.active_seconds == 68 and not session.running and session.ended_at


def test_sleep_gap_not_counted_and_minimize_equivalent_ticks_continue():
    clock = Clock()
    session = clock.session()
    clock.step(2)
    assert not session.tick()
    clock.step(3600)
    assert session.tick()
    assert session.active_seconds == 2 and not session.running
    session.resume()
    for _ in range(8):
        clock.step(0.25)
        session.tick()
    assert session.active_seconds == 4


def test_wall_clock_change_does_not_change_accumulated_duration():
    clock = Clock()
    session = clock.session()
    clock.step(2)
    session.tick()
    clock.value += 3
    clock.wall -= timedelta(hours=1)
    session.tick()
    assert session.active_seconds == 5
    assert sum(daily_seconds([session.to_dict()]).values()) == pytest.approx(5)


def test_restore_is_paused_and_does_not_count_closed_time():
    clock = Clock()
    session = clock.session()
    clock.step(3)
    session.tick()
    record = session.to_dict()
    clock.step(7200)
    restored = Session.restore(record, lambda: clock.value, lambda: clock.wall)
    restored.tick()
    assert not restored.running and restored.active_seconds == 3
    assert restored.last_completion is None
    restored.resume()
    clock.step(4)
    restored.tick()
    assert restored.active_seconds == 7


def test_midnight_allocation_matches_monotonic_total():
    clock = Clock(datetime(2026, 10, 4, 23, 59, 57, tzinfo=timezone(timedelta(hours=3))))
    session = clock.session()
    clock.step(8)
    session.finish()
    totals = daily_seconds([session.to_dict()])
    assert totals[("2026-10-04", "university")] == 3
    assert totals[("2026-10-05", "university")] == 5
    assert sum(totals.values()) == session.active_seconds


def test_counts_undo_and_history_upsert(tmp_path):
    session = Clock().session()
    action = Completion([
        {"id": "a", "text": "ДЗ", "parent_id": None, "before": False, "after": True},
        {"id": "b", "text": "26", "parent_id": "a", "before": False, "after": True},
    ])
    session.register(action)
    assert session.counts == (1, 1)
    session.undo_registered()
    assert session.counts == (0, 0)
    session.register(action)
    session.finish()
    store = StateStore(tmp_path)
    store.save_session(session.to_dict())
    store.save_session(session.to_dict())
    assert len(store.history()) == 1
    assert len(store.history()[0]["completed"]) == 2


@pytest.mark.parametrize("content", ['{bad json', '{"version":1,"sessions":[{}]}', '{"version":2,"sessions":[]}'])
def test_corrupt_state_never_overwritten(tmp_path, content):
    path = tmp_path / "sessions.json"
    path.write_text(content, encoding="utf-8")
    store = StateStore(tmp_path)
    with pytest.raises(StateError):
        store.save_session(Clock().session().to_dict())
    assert path.read_text(encoding="utf-8") == content


def test_invalid_snapshot_rejected():
    record = Clock().session().to_dict()
    record["active_seconds"] = float("inf")
    with pytest.raises(StateError):
        Session.restore(record)


def test_favorite_legacy_history_upsert_delete_and_totals(tmp_path):
    clock = Clock()
    session = clock.session()
    clock.step(5)
    session.finish()
    store = StateStore(tmp_path)
    original = session.to_dict()
    store.save_session(original)
    assert "favorite" not in store.history()[0]
    store.set_favorite(session.id, True)
    store.save_session(original)  # Recovery must retain the user's star.
    assert store.history()[0]["favorite"] is True
    assert sum(daily_seconds(store.history()).values()) == 5
    reopened = StateStore(tmp_path)
    assert reopened.history()[0]["favorite"] is True
    reopened.set_favorite(session.id, False)
    assert reopened.history()[0]["favorite"] is False
    reopened.delete_session(session.id)
    assert reopened.history() == [] and daily_seconds(reopened.history()) == {}


def test_history_mutation_write_failure_keeps_saved_record(tmp_path, monkeypatch):
    store = StateStore(tmp_path)
    record = Clock().session().to_dict()
    store.save_session(record)
    path = tmp_path / "sessions.json"
    payload = path.read_bytes()
    def fail(*args):
        raise StateError("Ошибка записи")
    monkeypatch.setattr(store, "write", fail)
    for action in (lambda: store.set_favorite(record["id"], True), lambda: store.delete_session(record["id"])):
        with pytest.raises(StateError):
            action()
        assert path.read_bytes() == payload


def test_invalid_favorite_and_unknown_history_id_are_not_saved(tmp_path):
    store = StateStore(tmp_path)
    record = Clock().session().to_dict()
    record["favorite"] = "yes"
    with pytest.raises(StateError):
        store.save_session(record)
    assert not (tmp_path / "sessions.json").exists()
    for operation in (lambda: store.delete_session("missing"), lambda: store.set_favorite("missing", True)):
        with pytest.raises(StateError):
            operation()
