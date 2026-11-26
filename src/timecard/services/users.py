import logging

from timecard.database.repositories import authorize, enqueue, open_session
from timecard.domain.errors import DomainError
from timecard.domain.models import stamp, utcnow


class UserService:
    def __init__(self, database, clock=utcnow):
        self.database, self.clock = database, clock

    def add(self, user, admin=False, name=None):
        if not user.startswith(("U", "W")) or not user.isalnum():
            raise DomainError("SlackユーザーIDを指定してください。")
        with self.database.transaction() as db:
            db.execute(
                """INSERT INTO users VALUES (?,?,1,?,?) ON CONFLICT(slack_user_id)
                DO UPDATE SET is_active=1,is_admin=excluded.is_admin,
                display_name=excluded.display_name""",
                (user, name or user, int(admin), stamp(self.clock())),
            )
            enqueue(db, user, self.clock())

    def disable(self, user):
        with self.database.transaction() as db:
            authorize(db, user)
            if open_session(db, user):
                raise DomainError("未終了セッションを本人が終了してから無効化してください。")
            db.execute("UPDATE users SET is_active=0 WHERE slack_user_id=?", (user,))
            enqueue(db, user, self.clock())

    def list(self):
        with self.database.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM users ORDER BY slack_user_id")]

    def require_admin(self, user):
        with self.database.connect() as db:
            if not authorize(db, user)["is_admin"]:
                raise DomainError("管理者権限が必要です。")

    def purge(self, actor, before):
        if before > self.clock():
            raise DomainError("削除期限は現在以前を指定してください。")
        with self.database.transaction() as db:
            if not authorize(db, actor)["is_admin"]:
                raise DomainError("管理者権限が必要です。")
            result = db.execute(
                "DELETE FROM sessions WHERE check_out_at IS NOT NULL AND check_out_at < ?",
                (stamp(before),),
            ).rowcount
            db.execute(
                "DELETE FROM operation_receipts WHERE session_id IS NULL AND created_at < ?",
                (stamp(before),),
            )
            db.execute(
                "DELETE FROM outbox WHERE processed_at IS NOT NULL AND created_at < ?",
                (stamp(before),),
            )
            enqueue(db, actor, self.clock())
        logging.getLogger(__name__).info("History purge actor=%s deleted=%d", actor, result)
        return result
