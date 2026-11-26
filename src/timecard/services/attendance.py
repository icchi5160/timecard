from timecard.database.repositories import (
    audit,
    authorize,
    enqueue,
    enqueue_notification_update,
    open_session,
)
from timecard.domain.errors import DomainError
from timecard.domain.models import parse, stamp, utcnow


class AttendanceService:
    def __init__(self, database, clock=utcnow):
        self.database, self.clock = database, clock

    def current(self, user):
        with self.database.connect() as db:
            authorize(db, user)
            row = open_session(db, user)
            return dict(row) if row else None

    def history(self, user, page=0, size=10):
        if page < 0 or not 1 <= size <= 20:
            raise DomainError("履歴のページ指定が不正です。")
        with self.database.connect() as db:
            authorize(db, user)
            return [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM sessions WHERE user_id=? "
                    "ORDER BY check_in_at DESC LIMIT ? OFFSET ?",
                    (user, size, page * size),
                )
            ]

    def owned(self, user, session_id):
        with self.database.connect() as db:
            authorize(db, user)
            row = db.execute(
                "SELECT * FROM sessions WHERE id=? AND user_id=?", (session_id, user)
            ).fetchone()
            if not row:
                raise DomainError("自分のセッションのみ操作できます。")
            return dict(row)

    def change(self, user, action, key, *, notification_id=None, checkout=None, session_id=None):
        if not key:
            raise ValueError("Idempotency key required")
        now = self.clock()
        now_s = stamp(now)
        with self.database.transaction() as db:
            authorize(db, user)
            previous = db.execute(
                "SELECT session_id,actor_user_id FROM operation_receipts WHERE idempotency_key=?",
                (key,),
            ).fetchone()
            if previous:
                if previous["actor_user_id"] != user:
                    raise DomainError("操作IDが不正です。")
                return previous["session_id"]
            session = open_session(db, user)
            notification = None
            if notification_id is not None:
                notification = db.execute(
                    """SELECT * FROM notifications WHERE id=? AND user_id=?
                    AND status IN ('SENT','EXPIRED')""",
                    (notification_id, user),
                ).fetchone()
                if (
                    not notification
                    or not session
                    or notification["session_id"] != session["id"]
                    or notification["cycle_key"] != session["last_confirmed_at"]
                ):
                    raise DomainError("この通知は無効です。最新の画面から操作してください。")
            if action == "check_in":
                if session:
                    if session["status"] == "UNCONFIRMED":
                        raise DomainError(
                            "現在、未確認です。App Homeか確認DMの「まだいる」で確認するか、"
                            "「退室する」を押してください。"
                        )
                    raise DomainError(
                        "既に在室中です。退室する場合は「退室する」を押してください。"
                    )
                session_id = db.execute(
                    """INSERT INTO sessions(user_id,check_in_at,last_confirmed_at,status,
                    created_at,updated_at) VALUES (?,?,?,'PRESENT',?,?)""",
                    (user, now_s, now_s, now_s, now_s),
                ).lastrowid
                kind = "CHECK_IN"
                metadata = {}
            elif action == "correct":
                session = db.execute(
                    "SELECT * FROM sessions WHERE id=? AND user_id=?", (session_id, user)
                ).fetchone()
                if not session or session["check_out_at"] is None:
                    raise DomainError("終了済みの自分のセッションを指定してください。")
                self._validate_checkout(db, session, checkout, now)
                metadata = {"before": session["check_out_at"], "after": stamp(checkout)}
                db.execute(
                    "UPDATE sessions SET check_out_at=?,updated_at=? WHERE id=?",
                    (stamp(checkout), now_s, session["id"]),
                )
                kind = "CORRECT_CHECK_OUT"
            else:
                if not session:
                    db.execute(
                        "INSERT INTO operation_receipts VALUES (?,?,NULL,?)",
                        (key, user, now_s),
                    )
                    return None
                if session_id is not None and session_id != session["id"]:
                    raise DomainError("セッションが変わりました。画面を開き直してください。")
                session_id = session["id"]
                metadata = {}
                if action == "check_out":
                    end = checkout if checkout is not None else now
                    self._validate_checkout(db, session, end, now)
                    db.execute(
                        "UPDATE sessions SET check_out_at=?,status='ABSENT',updated_at=? "
                        "WHERE id=?",
                        (stamp(end), now_s, session_id),
                    )
                    metadata = {"check_out_at": stamp(end)}
                    kind = "CHECK_OUT"
                elif action == "confirm":
                    db.execute(
                        """UPDATE sessions SET status='PRESENT',last_confirmed_at=?,updated_at=?
                        WHERE id=?""",
                        (now_s, now_s, session_id),
                    )
                    kind = "CONFIRM"
                else:
                    raise ValueError("Unknown attendance action")
                cancelled = db.execute(
                    """UPDATE notifications SET status='CANCELLED'
                    WHERE session_id=? AND status IN
                    ('PENDING','SENDING','SENT','EXPIRED','FAILED') RETURNING id""",
                    (session_id,),
                ).fetchall()
                if notification:
                    db.execute(
                        "UPDATE notifications SET status='ANSWERED',responded_at=? WHERE id=?",
                        (now_s, notification_id),
                    )
                for notice in cancelled:
                    enqueue_notification_update(db, notice["id"], now)
            db.execute(
                "INSERT INTO operation_receipts VALUES (?,?,?,?)", (key, user, session_id, now_s)
            )
            audit(db, session_id, user, kind, now, key, metadata)
            enqueue(db, user, now)
            return session_id

    @staticmethod
    def _validate_checkout(db, session, checkout, now):
        if checkout is None or not parse(session["check_in_at"]) <= checkout <= now:
            raise DomainError("退室日時は入室日時以降、現在以前で指定してください。")
        end = stamp(checkout)
        overlap = db.execute(
            """SELECT 1 FROM sessions WHERE user_id=? AND id<>?
            AND check_in_at < ? AND (check_out_at IS NULL OR check_out_at > ?)""",
            (session["user_id"], session["id"], end, session["check_in_at"]),
        ).fetchone()
        if overlap:
            raise DomainError("他のセッションと時間が重複しています。")
