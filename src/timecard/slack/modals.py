import json
from datetime import datetime

from timecard.domain.errors import DomainError
from timecard.services.statistics import JST


def checkout_modal(metadata, now):
    local = now.astimezone(JST)
    return {
        "type": "modal",
        "callback_id": "timecard_checkout",
        "private_metadata": json.dumps(metadata),
        "title": {"type": "plain_text", "text": "退室日時の指定"},
        "submit": {"type": "plain_text", "text": "保存"},
        "close": {"type": "plain_text", "text": "キャンセル"},
        "blocks": [
            {
                "type": "input",
                "block_id": "date",
                "label": {"type": "plain_text", "text": "退室日（日本時間）"},
                "element": {
                    "type": "datepicker",
                    "action_id": "date",
                    "initial_date": local.strftime("%Y-%m-%d"),
                },
            },
            {
                "type": "input",
                "block_id": "time",
                "label": {"type": "plain_text", "text": "退室時刻（日本時間）"},
                "element": {
                    "type": "timepicker",
                    "action_id": "time",
                    "initial_time": local.strftime("%H:%M"),
                },
            },
        ],
    }


def submitted_time(view):
    try:
        values = view["state"]["values"]
        date = values["date"]["date"]["selected_date"]
        time = values["time"]["time"]["selected_time"]
        return datetime.fromisoformat(f"{date}T{time}").replace(tzinfo=JST)
    except (KeyError, ValueError, TypeError) as exc:
        raise DomainError("日付と時刻を入力してください。") from exc
