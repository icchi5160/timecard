import hashlib
import json
import logging
import re

from timecard.database.repositories import authorize
from timecard.domain.errors import DomainError
from timecard.slack.blocks import text
from timecard.slack.modals import checkout_modal, submitted_time

logger = logging.getLogger(__name__)


def team_id(body):
    team = body.get("team")
    return (team.get("id") if isinstance(team, dict) else team) or body.get("team_id")


def require_access(body, config, database):
    if team_id(body) != config.team_id:
        raise DomainError("このワークスペースでは利用できません。")
    user = body["user"]["id"] if isinstance(body.get("user"), dict) else body["event"]["user"]
    with database.connect() as db:
        authorize(db, user)
    channel = body.get("channel", {}).get("id")
    if channel and channel != config.channel_id and not channel.startswith("D"):
        raise DomainError("指定チャンネル、App Home、DMから操作してください。")
    return user


def operation_key(body):
    # Stable across Slack redelivery, distinct across actual button presses.
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


def register(app, config, database, attendance, sync):
    @app.action(re.compile(r"^timecard_"))
    def handle_action(ack, body, client):
        ack()
        try:
            user = require_access(body, config, database)
            action = body["actions"][0]
            name, value = action["action_id"], action.get("value", "")
            key = operation_key(body)
            if name == "timecard_open_history":
                sync.publish_home(user, int(value))
                return
            if name == "timecard_edit_checkout":
                kind, raw = value.split(":", 1)
                ident = int(raw)
                if kind == "s":
                    session = attendance.owned(user, ident)
                    if not session["check_out_at"]:
                        raise DomainError("終了済みセッションを選択してください。")
                    metadata = {"action": "correct", "session_id": ident}
                elif kind == "n":
                    with database.connect() as db:
                        notice = db.execute(
                            """SELECT n.* FROM notifications n JOIN sessions s ON s.id=n.session_id
                            WHERE n.id=? AND n.user_id=? AND n.status IN ('SENT','EXPIRED')
                            AND s.check_out_at IS NULL AND n.cycle_key=s.last_confirmed_at""",
                            (ident, user),
                        ).fetchone()
                    if not notice:
                        raise DomainError("この通知は無効です。")
                    metadata = {
                        "action": "check_out",
                        "notification_id": ident,
                        "session_id": notice["session_id"],
                    }
                else:
                    raise DomainError("不正な操作です。")
                metadata["user"] = user
                client.views_open(
                    trigger_id=body["trigger_id"], view=checkout_modal(metadata, attendance.clock())
                )
                return
            mapping = {
                "timecard_check_in": "check_in",
                "timecard_check_out": "check_out",
                "timecard_confirm_presence": "confirm",
                "timecard_confirm_checkout": "check_out",
            }
            if name not in mapping:
                raise DomainError("不明な操作です。")
            notification_id = int(value) if value.isdigit() else None
            attendance.change(user, mapping[name], key, notification_id=notification_id)
            # Ack has already been sent; durable failures are retried by the scheduler.
            sync.process()
        except (DomainError, ValueError, KeyError) as exc:
            message = str(exc) if isinstance(exc, DomainError) else "入力が不正です。"
            channel = body.get("channel", {}).get("id")
            user_id = body.get("user", {}).get("id")
            # Do not call any API for an untrusted workspace.
            if team_id(body) == config.team_id and channel and user_id:
                client.chat_postEphemeral(channel=channel, user=user_id, text=message)
            elif team_id(body) == config.team_id and user_id and body.get("view"):
                try:
                    require_access(body, config, database)
                except DomainError:
                    client.views_publish(
                        user_id=user_id, view={"type": "home", "blocks": [text(message)]}
                    )
                else:
                    sync.publish_home(user_id, notice=message)
            logger.info("Rejected Slack action")

    @app.event("app_home_opened")
    def handle_home(body):
        try:
            user = require_access(body, config, database)
            sync.publish_home(user)
        except DomainError:
            if team_id(body) == config.team_id:
                sync.client.views_publish(
                    user_id=body["event"]["user"],
                    view={"type": "home", "blocks": [text("利用には管理者による登録が必要です。")]},
                )
            logger.info("Rejected App Home request")

    @app.view("timecard_checkout")
    def handle_checkout(ack, body, view):
        try:
            user = require_access(body, config, database)
            metadata = json.loads(view["private_metadata"])
            if metadata.pop("user") != user:
                raise DomainError("自分の履歴のみ操作できます。")
            checkout = submitted_time(view)
            attendance.change(user, key="modal:" + view["id"], checkout=checkout, **metadata)
        except (DomainError, ValueError, KeyError, TypeError):
            ack(
                response_action="errors",
                errors={"date": "退室日時・所有者・通知の有効性を確認してください。"},
            )
        else:
            ack()
            sync.process()
