import json

from timecard.domain.errors import DomainError
from timecard.domain.models import stamp


def authorize(db, user):
    row = db.execute(
        "SELECT * FROM users WHERE slack_user_id=? AND is_active=1", (user,)
    ).fetchone()
    if row is None:
        raise DomainError("登録済みの有効なユーザーのみ利用できます。")
    return row


def open_session(db, user):
    return db.execute(
        "SELECT * FROM sessions WHERE user_id=? AND check_out_at IS NULL", (user,)
    ).fetchone()


def enqueue(db, user, now):
    db.execute(
        "INSERT INTO outbox(event_type,payload_json,created_at) VALUES ('SYNC',?,?)",
        (json.dumps({"user": user}), stamp(now)),
    )


def enqueue_notification_update(db, notification_id, now):
    db.execute(
        "INSERT INTO outbox(event_type,payload_json,created_at) VALUES ('NOTIFICATION_UPDATE',?,?)",
        (json.dumps({"notification_id": notification_id}), stamp(now)),
    )


def audit(db, session, user, kind, now, key=None, metadata=None):
    db.execute(
        """INSERT INTO attendance_events
        (session_id,actor_user_id,event_type,occurred_at,metadata_json,idempotency_key)
        VALUES (?,?,?,?,?,?)""",
        (session, user, kind, stamp(now), json.dumps(metadata or {}), key),
    )
