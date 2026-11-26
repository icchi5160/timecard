from datetime import UTC, datetime

import pytest

from timecard.domain.models import parse, stamp
from timecard.services.statistics import JST


@pytest.mark.parametrize(
    "start,end,day,week,month",
    [
        ("2026-01-05T09:00", "2026-01-05T10:30", 5400, 5400, 5400),
        ("2026-01-04T23:00", "2026-01-05T01:00", 3600, 3600, 7200),
        ("2026-01-31T23:00", "2026-02-01T01:00", 3600, 7200, 3600),
        ("2026-02-01T23:00", "2026-02-02T01:00", 3600, 3600, 7200),
    ],
)
def test_period_boundaries(env, start, end, day, week, month):
    _, clock, attendance, stats, _ = env
    clock.now = datetime.fromisoformat(start).replace(tzinfo=JST)
    attendance.change("U1", "check_in", "in")
    clock.now = datetime.fromisoformat(end).replace(tzinfo=JST)
    attendance.change("U1", "check_out", "out")
    assert stats.summary("U1") == {"今日": day, "今週": week, "今月": month}


def test_multiple_sessions_and_half_open_boundary(env):
    _, clock, attendance, stats, _ = env
    start = clock()
    attendance.change("U1", "check_in", "in")
    clock.advance(hours=1)
    attendance.change("U1", "check_out", "out")
    end = clock()
    attendance.change("U1", "check_in", "in2")
    clock.advance(hours=2)
    attendance.change("U1", "check_out", "out2")
    assert stats.summary("U1")["今日"] == 10800
    assert stats.total("U1", start, end) == 3600


def test_utc_jst_and_naive_rejected():
    local = datetime(2026, 1, 1, 0, tzinfo=JST)
    assert parse(stamp(local)) == datetime(2025, 12, 31, 15, tzinfo=UTC)
    with pytest.raises(ValueError):
        stamp(datetime(2026, 1, 1))
