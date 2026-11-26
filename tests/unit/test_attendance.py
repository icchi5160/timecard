from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import patch

import pytest

from timecard.domain.errors import DomainError
from timecard.services.attendance import AttendanceService


def test_transitions_and_replay(env):
    db, clock, service, stats, _ = env
    sid = service.change("U1", "check_in", "in")
    assert service.current("U1")["status"] == "PRESENT"
    assert service.change("U1", "check_in", "in") == sid
    with pytest.raises(DomainError):
        service.change("U1", "check_in", "second")
    clock.advance(hours=2)
    assert stats.summary("U1")["今日"] == 0
    service.change("U1", "confirm", "confirm")
    assert service.current("U1")["last_confirmed_at"].startswith("2026-01-05T02")
    service.change("U1", "check_out", "out")
    service.change("U1", "check_out", "out2")
    assert service.current("U1") is None
    assert stats.summary("U1")["今日"] == 7200
    service.change("U1", "check_in", "in2")
    # A redelivered absent checkout must not close a later session.
    service.change("U1", "check_out", "out2")
    assert service.current("U1") is not None
    with db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM sessions").fetchone()[0] == 2


@pytest.mark.parametrize("same_key", [False, True])
def test_concurrent_check_in(env, same_key):
    db, _, service, _, _ = env

    def enter(i):
        try:
            return service.change("U1", "check_in", "key" if same_key else str(i))
        except DomainError:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(enter, range(8)))
    assert len(set(x for x in ids if x is not None)) == 1
    with db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM sessions").fetchone()[0] == 1


def test_transaction_rolls_back_event_and_outbox(env):
    db, _, service, _, _ = env
    with db.connect() as conn:
        before = conn.execute("SELECT count(*) FROM outbox").fetchone()[0]
    with patch("timecard.services.attendance.enqueue", side_effect=RuntimeError("disk error")):
        with pytest.raises(RuntimeError):
            service.change("U1", "check_in", "failed")
    with db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM sessions").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM attendance_events").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM outbox").fetchone()[0] == before
    service.change("U1", "check_in", "failed")


def test_restart_keeps_session_and_idempotency(env):
    db, clock, service, _, _ = env
    sid = service.change("U1", "check_in", "in")
    db.migrate()
    restarted = AttendanceService(db, clock)
    assert restarted.change("U1", "check_in", "in") == sid
    assert restarted.current("U1")["id"] == sid


def test_correction_ownership_validation_and_audit(env):
    db, clock, service, stats, _ = env
    start = clock()
    sid = service.change("U1", "check_in", "in")
    clock.advance(hours=2)
    service.change("U1", "check_out", "out")
    for end in (start - timedelta(minutes=1), clock() + timedelta(minutes=1)):
        with pytest.raises(DomainError):
            service.change("U1", "correct", str(end), session_id=sid, checkout=end)
    with pytest.raises(DomainError):
        service.change("U2", "correct", "foreign", session_id=sid, checkout=clock())
    service.change("U1", "correct", "fix", session_id=sid, checkout=start + timedelta(hours=1))
    assert stats.summary("U1")["今日"] == 3600
    with db.connect() as conn:
        metadata = conn.execute(
            "SELECT metadata_json FROM attendance_events WHERE event_type='CORRECT_CHECK_OUT'"
        ).fetchone()[0]
        assert '"before"' in metadata and '"after"' in metadata


def test_overlap_and_stale_modal_session(env):
    _, clock, service, _, _ = env
    first = service.change("U1", "check_in", "in")
    clock.advance(hours=1)
    service.change("U1", "check_out", "out")
    clock.advance(hours=1)
    second = service.change("U1", "check_in", "in2")
    clock.advance(hours=1)
    with pytest.raises(DomainError):
        service.change("U1", "correct", "overlap", session_id=first, checkout=clock())
    with pytest.raises(DomainError):
        service.change("U1", "check_out", "stale", session_id=first, checkout=clock())
    assert service.current("U1")["id"] == second


@pytest.mark.parametrize("user", ["U3", "U2"])
def test_access(env, user):
    _, _, service, _, users = env
    if user == "U2":
        users.disable(user)
    with pytest.raises(DomainError):
        service.change(user, "check_in", "in")


def test_disable_and_admin(env):
    _, clock, service, _, users = env
    service.change("U1", "check_in", "in")
    with pytest.raises(DomainError):
        users.disable("U1")
    with pytest.raises(DomainError):
        users.purge("U2", clock())
    users.require_admin("U1")
