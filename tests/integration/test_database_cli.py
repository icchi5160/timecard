import fcntl
import sqlite3
import subprocess
from datetime import timedelta

import pytest

from timecard.config import Config
from timecard.database.connection import Database
from timecard.domain.models import stamp
from timecard.main import run


def test_migrations_and_database_permissions(env):
    db, _, _, _, _ = env
    db.migrate()
    assert db.path.stat().st_mode & 0o777 == 0o600
    with db.connect() as conn:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute("SELECT count(*) FROM schema_migrations").fetchone()[0] == 2


def test_sql_constraints(env):
    db, clock, attendance, _, _ = env
    attendance.change("U1", "check_in", "in")
    with db.transaction() as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """INSERT INTO sessions
                (user_id,check_in_at,last_confirmed_at,status,created_at,updated_at)
                VALUES ('U1',?,?,'PRESENT',?,?)""",
                (stamp(clock()),) * 4,
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE sessions SET status='ABSENT' WHERE user_id='U1'")


def test_retention_cascades_and_preserves_open(env):
    db, clock, attendance, _, users = env
    sid = attendance.change("U1", "check_in", "in")
    clock.advance(hours=1)
    attendance.change("U1", "check_out", "out")
    attendance.change("U2", "check_in", "open")
    clock.advance(days=91)
    assert users.purge("U1", clock() - timedelta(days=90)) == 1
    assert attendance.current("U2")
    with db.connect() as conn:
        assert conn.execute("SELECT 1 FROM sessions WHERE id=?", (sid,)).fetchone() is None
        assert (
            conn.execute(
                "SELECT count(*) FROM attendance_events WHERE session_id=?", (sid,)
            ).fetchone()[0]
            == 0
        )
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_cli_without_tokens(tmp_path, monkeypatch):
    db = tmp_path / "cli.db"
    monkeypatch.setenv("DATABASE_PATH", str(db))
    for args in [
        ["init-db"],
        ["add-user", "U1", "--admin"],
        ["list-users"],
        ["disable-user", "U1"],
    ]:
        result = subprocess.run(["timecard", *args], capture_output=True, text=True, check=False)
        assert result.returncode == 0, result.stderr
    assert Database(db).path.exists()


def test_bot_duplicate_lock_rejected(env):
    db, _, _, _, _ = env
    config = Config(bot_token="xoxb-test", app_token="xapp-test", team_id="T1", channel_id="C1")
    with open(str(db.path) + ".bot.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ValueError, match="既に"):
            run(config, db)


def test_config_validation(monkeypatch):
    monkeypatch.setenv("CONFIRMATION_INTERVAL_MINUTES", "0")
    with pytest.raises(ValueError):
        Config.load()
    with pytest.raises(ValueError):
        Config().validate_slack()
