import fcntl
import os
import signal
import threading

from slack_bolt.adapter.socket_mode import SocketModeHandler
from slack_sdk import WebClient
from slack_sdk.http_retry.builtin_handlers import (
    ConnectionErrorRetryHandler,
    RateLimitErrorRetryHandler,
    ServerErrorRetryHandler,
)

from timecard.database.repositories import enqueue
from timecard.domain.models import utcnow
from timecard.scheduler.jobs import create_scheduler
from timecard.services.attendance import AttendanceService
from timecard.services.notifications import NotificationService
from timecard.services.statistics import StatisticsService
from timecard.slack.app import create_app
from timecard.slack.sync import SlackSync


def run(config, database):
    config.validate_slack()
    # Held for the full process lifetime; CLI transactions do not acquire this lock.
    with open(str(database.path) + ".bot.lock", "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("このDBのBotは既に起動しています。") from exc
        os.chmod(lock.name, 0o600)
        database.migrate()
        client = WebClient(
            token=config.bot_token,
            timeout=15,
            retry_handlers=[
                ConnectionErrorRetryHandler(max_retry_count=2),
                RateLimitErrorRetryHandler(max_retry_count=2),
                ServerErrorRetryHandler(max_retry_count=2),
            ],
        )
        identity = client.auth_test()
        if identity["team_id"] != config.team_id:
            raise ValueError("Bot token workspace does not match SLACK_TEAM_ID")
        attendance = AttendanceService(database)
        sync = SlackSync(database, client, config, attendance, StatisticsService(database))
        notifications = NotificationService(
            database, sync.send_confirmation, config.interval, config.timeout
        )
        app = create_app(config, database, attendance, sync)
        handler = SocketModeHandler(app, config.app_token)
        scheduler, tick = create_scheduler(notifications, sync)
        stop = threading.Event()
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: stop.set())
        notifications.recover()
        with database.transaction() as db:
            enqueue(db, None, utcnow())
        try:
            handler.connect()
            scheduler.start()
            tick()
            stop.wait()
        finally:
            if scheduler.running:
                scheduler.shutdown(wait=True)
            handler.close()
