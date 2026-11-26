import json
import logging
from datetime import timedelta
from threading import RLock

from slack_sdk.errors import SlackApiError

from timecard.domain.models import parse, stamp, utcnow
from timecard.slack.blocks import completed_confirmation, confirmation, dashboard
from timecard.slack.home import render

logger = logging.getLogger(__name__)


class SlackSync:
    def __init__(self, database, client, config, attendance, statistics, clock=utcnow):
        self.database, self.client, self.config = database, client, config
        self.attendance, self.statistics, self.clock = attendance, statistics, clock
        self._lock = RLock()

    def publish_home(self, user, page=0, notice=None):
        self.client.views_publish(
            user_id=user, view=render(self.attendance, self.statistics, user, page, notice)
        )

    def send_confirmation(self, notice):
        dm = self.client.conversations_open(users=notice["user_id"])
        result = self.client.chat_postMessage(
            channel=dm["channel"]["id"],
            text="timecard：在室確認",
            blocks=confirmation(notice),
        )
        return result["ts"]

    def dashboard(self):
        # Settings are scoped to the configured team/channel to prevent cross-environment reuse.
        scope = self.config.team_id + ":" + self.config.channel_id
        with self.database.connect() as db:
            settings = dict(db.execute("SELECT key,value FROM app_settings"))
        ts = (
            settings.get("dashboard_message_ts")
            if settings.get("dashboard_scope") == scope
            else None
        )
        blocks = dashboard(self.database)
        if ts:
            try:
                self.client.chat_update(
                    channel=self.config.channel_id, ts=ts, text="timecard 在室状況", blocks=blocks
                )
                return
            except SlackApiError as exc:
                if exc.response["error"] != "message_not_found":
                    raise
        response = self.client.chat_postMessage(
            channel=self.config.channel_id, text="timecard 在室状況", blocks=blocks
        )
        with self.database.transaction() as db:
            for key, value in [
                ("dashboard_message_ts", response["ts"]),
                ("dashboard_scope", scope),
            ]:
                db.execute(
                    "INSERT OR REPLACE INTO app_settings(key,value) VALUES (?,?)", (key, value)
                )

    def update_notification(self, notification_id):
        with self.database.connect() as db:
            row = db.execute(
                "SELECT * FROM notifications WHERE id=?", (notification_id,)
            ).fetchone()
        if not row or row["status"] not in ("ANSWERED", "CANCELLED") or not row["slack_message_ts"]:
            return
        dm = self.client.conversations_open(users=row["user_id"])
        try:
            self.client.chat_update(
                channel=dm["channel"]["id"],
                ts=row["slack_message_ts"],
                text="timecard：在室確認（回答済み）"
                if row["status"] == "ANSWERED"
                else "timecard：在室確認（終了）",
                blocks=completed_confirmation(row),
            )
        except SlackApiError as exc:
            if exc.response["error"] != "message_not_found":
                raise

    def process(self):
        # Button listeners and the scheduler may run concurrently in one Bot process.
        with self._lock:
            self._process()

    def _process(self):
        with self.database.connect() as db:
            rows = [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM outbox WHERE processed_at IS NULL ORDER BY id LIMIT 100"
                )
            ]
        for row in rows:
            payload = json.loads(row["payload_json"])
            if payload.get("retry_at") and parse(payload["retry_at"]) > self.clock():
                continue
            try:
                if row["event_type"] == "NOTIFICATION_UPDATE":
                    self.update_notification(payload["notification_id"])
                else:
                    self.dashboard()
                user = payload.get("user")
                if user:
                    with self.database.connect() as db:
                        active = db.execute(
                            "SELECT is_active FROM users WHERE slack_user_id=?", (user,)
                        ).fetchone()
                    if active and active["is_active"]:
                        self.publish_home(user)
            except Exception:
                logger.warning("Slack sync failed outbox_id=%s", row["id"])
                delay = min(3600, 60 * 2 ** min(row["attempt_count"], 6))
                payload["retry_at"] = stamp(self.clock() + timedelta(seconds=delay))
                with self.database.transaction() as db:
                    db.execute(
                        "UPDATE outbox SET attempt_count=attempt_count+1,payload_json=? WHERE id=?",
                        (json.dumps(payload), row["id"]),
                    )
            else:
                with self.database.transaction() as db:
                    db.execute(
                        "UPDATE outbox SET processed_at=? WHERE id=?",
                        (stamp(self.clock()), row["id"]),
                    )
