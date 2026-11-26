import logging
from datetime import timedelta

from timecard.database.repositories import audit, enqueue, enqueue_notification_update
from timecard.domain.models import parse, stamp, utcnow

logger = logging.getLogger(__name__)


class NotificationService:
    """The injected sender returns a Slack message timestamp; no SDK dependency."""

    def __init__(self, database, sender, interval=360, timeout=30, clock=utcnow):
        self.database, self.sender, self.clock = database, sender, clock
        self.interval, self.timeout = timedelta(minutes=interval), timedelta(minutes=timeout)

    def recover(self):
        # Only called once after obtaining the process lock.
        with self.database.transaction() as db:
            db.execute("UPDATE notifications SET status='FAILED' WHERE status='SENDING'")

    def tick(self):
        now = self.clock()
        with self.database.transaction() as db:
            sessions = db.execute(
                """SELECT s.* FROM sessions s JOIN users u ON u.slack_user_id=s.user_id
                WHERE s.status='PRESENT' AND s.check_out_at IS NULL AND u.is_active=1
                AND s.last_confirmed_at<=?""",
                (stamp(now - self.interval),),
            ).fetchall()
            for s in sessions:
                db.execute(
                    """INSERT OR IGNORE INTO notifications
                    (session_id,user_id,cycle_key,scheduled_at,status)
                    VALUES (?,?,?,?,'PENDING')""",
                    (
                        s["id"],
                        s["user_id"],
                        s["last_confirmed_at"],
                        stamp(parse(s["last_confirmed_at"]) + self.interval),
                    ),
                )
            expired = db.execute(
                """SELECT n.* FROM notifications n JOIN sessions s ON s.id=n.session_id
                WHERE n.status='SENT' AND n.sent_at<=? AND s.check_out_at IS NULL
                AND n.cycle_key=s.last_confirmed_at""",
                (stamp(now - self.timeout),),
            ).fetchall()
            for n in expired:
                db.execute("UPDATE notifications SET status='EXPIRED' WHERE id=?", (n["id"],))
                db.execute(
                    "UPDATE sessions SET status='UNCONFIRMED',updated_at=? WHERE id=?",
                    (stamp(now), n["session_id"]),
                )
                audit(
                    db, n["session_id"], n["user_id"], "MARK_UNCONFIRMED", now, f"timeout:{n['id']}"
                )
                enqueue(db, n["user_id"], now)
            pending = [
                r["id"]
                for r in db.execute(
                    "SELECT id FROM notifications WHERE status IN ('PENDING','FAILED') ORDER BY id"
                )
            ]
        for notification_id in pending:
            self._deliver(notification_id)

    def _deliver(self, notification_id):
        now = self.clock()
        with self.database.transaction() as db:
            row = db.execute(
                """SELECT n.*,s.check_in_at,s.last_confirmed_at,s.check_out_at
                FROM notifications n JOIN sessions s ON s.id=n.session_id
                WHERE n.id=? AND n.status IN ('PENDING','FAILED')""",
                (notification_id,),
            ).fetchone()
            if row is None:
                return
            if row["check_out_at"] or row["cycle_key"] != row["last_confirmed_at"]:
                db.execute(
                    "UPDATE notifications SET status='CANCELLED' WHERE id=?", (notification_id,)
                )
                return
            # scheduled_at doubles as persisted retry time until delivery succeeds.
            if parse(row["scheduled_at"]) > now:
                return
            db.execute(
                "UPDATE notifications SET status='SENDING',attempt_count=attempt_count+1 "
                "WHERE id=?",
                (notification_id,),
            )
            notice = dict(row)
        try:
            ts = self.sender(notice)
        except Exception:
            logger.warning("Notification delivery failed id=%s", notification_id)
            delay = min(3600, 60 * 2 ** min(notice["attempt_count"], 6))
            with self.database.transaction() as db:
                db.execute(
                    """UPDATE notifications SET status='FAILED',scheduled_at=?
                    WHERE id=? AND status='SENDING'""",
                    (stamp(now + timedelta(seconds=delay)), notification_id),
                )
        else:
            with self.database.transaction() as db:
                delivered = db.execute(
                    """UPDATE notifications SET
                    status=CASE WHEN status='SENDING' THEN 'SENT' ELSE status END,
                    sent_at=?,slack_message_ts=? WHERE id=? RETURNING status""",
                    (stamp(self.clock()), ts, notification_id),
                ).fetchone()
                if delivered and delivered["status"] in ("ANSWERED", "CANCELLED"):
                    enqueue_notification_update(db, notification_id, self.clock())
