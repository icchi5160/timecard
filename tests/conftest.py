from datetime import UTC, datetime, timedelta

import pytest

from timecard.database.connection import Database
from timecard.services.attendance import AttendanceService
from timecard.services.statistics import StatisticsService
from timecard.services.users import UserService


class Clock:
    def __init__(self):
        self.now = datetime(2026, 1, 5, 0, tzinfo=UTC)

    def __call__(self):
        return self.now

    def advance(self, **kwargs):
        self.now += timedelta(**kwargs)


@pytest.fixture
def env(tmp_path):
    clock = Clock()
    database = Database(tmp_path / "timecard.db")
    database.migrate()
    users = UserService(database, clock)
    users.add("U1", admin=True)
    users.add("U2")
    attendance = AttendanceService(database, clock)
    statistics = StatisticsService(database, clock)
    return database, clock, attendance, statistics, users
