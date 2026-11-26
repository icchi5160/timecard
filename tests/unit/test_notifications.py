from unittest.mock import Mock

import pytest

from timecard.domain.errors import DomainError
from timecard.services.notifications import NotificationService


def setup(env, sender=None):
    db, clock, service, _, _ = env
    sender = sender if sender is not None else Mock(return_value="123.4")
    notifications = NotificationService(db, sender, clock=clock)
    service.change("U1", "check_in", "in")
    return notifications, sender


def notices(db):
    with db.connect() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM notifications ORDER BY id")]


@pytest.mark.parametrize("response", ["confirm", "check_out"])
def test_notification_timeout_and_response(env, response):
    db, clock, service, stats, _ = env
    job, sender = setup(env)
    clock.advance(hours=5, minutes=59)
    job.tick()
    sender.assert_not_called()
    clock.advance(minutes=1)
    job.tick()
    n = notices(db)[0]
    assert n["status"] == "SENT"
    job.tick()
    sender.assert_called_once()
    clock.advance(minutes=29)
    job.tick()
    assert service.current("U1")["status"] == "PRESENT"
    clock.advance(minutes=1)
    job.tick()
    assert service.current("U1")["status"] == "UNCONFIRMED"
    assert stats.summary("U1")["今日"] == 0
    job.tick()
    sender.assert_called_once()
    service.change("U1", response, "response", notification_id=n["id"])
    assert notices(db)[0]["status"] == "ANSWERED"
    if response == "confirm":
        assert service.current("U1")["status"] == "PRESENT"
        clock.advance(hours=6)
        job.tick()
        assert len(notices(db)) == 2
    else:
        assert service.current("U1") is None
    with pytest.raises(DomainError):
        service.change("U1", "confirm", "stale", notification_id=n["id"])


def test_checkout_cancels_and_stale_response_rejected(env):
    db, clock, service, _, _ = env
    job, _ = setup(env)
    clock.advance(hours=6)
    job.tick()
    n = notices(db)[0]
    service.change("U1", "check_out", "out")
    assert notices(db)[0]["status"] == "CANCELLED"
    service.change("U1", "check_in", "in2")
    with pytest.raises(DomainError):
        service.change("U1", "check_out", "old", notification_id=n["id"])


def test_failed_delivery_does_not_timeout_and_retries(env):
    db, clock, service, _, _ = env
    sender = Mock(side_effect=RuntimeError("network"))
    job, _ = setup(env, sender)
    clock.advance(hours=6)
    job.tick()
    assert notices(db)[0]["status"] == "FAILED"
    assert notices(db)[0]["sent_at"] is None
    clock.advance(minutes=31)
    job.tick()
    assert service.current("U1")["status"] == "PRESENT"
    sender.side_effect = None
    sender.return_value = "ts"
    clock.advance(hours=1)
    job.tick()
    assert notices(db)[0]["status"] == "SENT"
    assert len(notices(db)) == 1


def test_crash_after_send_recovery_is_idempotent(env):
    db, clock, service, _, _ = env

    def send_then_crash(notice):
        raise SystemExit("process died after Slack accepted DM")

    job, _ = setup(env, Mock(side_effect=send_then_crash))
    clock.advance(hours=6)
    with pytest.raises(SystemExit):
        job.tick()
    n = notices(db)[0]
    assert n["status"] == "SENDING"
    replacement = NotificationService(db, Mock(return_value="duplicate-ts"), clock=clock)
    replacement.recover()
    replacement.tick()
    assert notices(db)[0]["status"] == "SENT"
    assert len(notices(db)) == 1
    service.change("U1", "confirm", "response", notification_id=n["id"])
    service.change("U1", "confirm", "response", notification_id=n["id"])
    with db.connect() as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM attendance_events WHERE event_type='CONFIRM'"
            ).fetchone()[0]
            == 1
        )


def test_checkout_while_delivery_in_flight(env):
    db, clock, service, _, _ = env

    def sender(notice):
        service.change("U1", "check_out", "out")
        return "ts"

    job, _ = setup(env, sender)
    clock.advance(hours=6)
    job.tick()
    assert notices(db)[0]["status"] == "CANCELLED"
    assert notices(db)[0]["slack_message_ts"] == "ts"
    with db.connect() as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM outbox WHERE event_type='NOTIFICATION_UPDATE'"
            ).fetchone()[0]
            >= 1
        )
    clock.advance(hours=1)
    job.tick()
    assert service.current("U1") is None
