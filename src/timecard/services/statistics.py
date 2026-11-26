from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from timecard.database.repositories import authorize
from timecard.domain.models import parse, stamp, utcnow

JST = ZoneInfo("Asia/Tokyo")


class StatisticsService:
    def __init__(self, database, clock=utcnow):
        self.database, self.clock = database, clock

    def total(self, user, start, end):
        stamp(start)
        stamp(end)
        if end <= start:
            raise ValueError("Invalid period")
        with self.database.connect() as db:
            authorize(db, user)
            sessions = db.execute(
                """SELECT * FROM sessions WHERE user_id=? AND check_out_at IS NOT NULL
                AND check_in_at < ? AND check_out_at > ?""",
                (user, stamp(end), stamp(start)),
            )
            return sum(
                max(
                    0,
                    (
                        min(parse(r["check_out_at"]), end) - max(parse(r["check_in_at"]), start)
                    ).total_seconds(),
                )
                for r in sessions
            )

    def summary(self, user):
        now = self.clock().astimezone(JST)
        day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week = day - timedelta(days=day.weekday())
        month = day.replace(day=1)
        next_month = datetime(month.year + (month.month == 12), month.month % 12 + 1, 1, tzinfo=JST)
        return {
            "今日": self.total(user, day, day + timedelta(days=1)),
            "今週": self.total(user, week, week + timedelta(days=7)),
            "今月": self.total(user, month, next_month),
        }
