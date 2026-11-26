from timecard.domain.models import parse
from timecard.services.statistics import JST


def text(value):
    return {"type": "section", "text": {"type": "mrkdwn", "text": value}}


def button(label, action, value="main"):
    return {
        "type": "button",
        "text": {"type": "plain_text", "text": label},
        "action_id": "timecard_" + action,
        "value": str(value),
    }


def actions(*items):
    return {"type": "actions", "elements": list(items)}


def local(value):
    return parse(value).astimezone(JST).strftime("%Y-%m-%d %H:%M")


def duration(seconds):
    minutes = int(seconds // 60)
    return f"{minutes // 60}時間{minutes % 60}分"


def dashboard(database):
    with database.connect() as db:
        rows = db.execute(
            """SELECT u.slack_user_id,s.status,s.check_in_at,s.last_confirmed_at
            FROM users u LEFT JOIN sessions s ON s.user_id=u.slack_user_id
            AND s.check_out_at IS NULL WHERE u.is_active=1 ORDER BY u.slack_user_id"""
        ).fetchall()
    blocks = [text("*timecard*")]
    for status, label in [
        ("PRESENT", "🟢 在室中"),
        ("UNCONFIRMED", "🟡 未確認"),
        (None, "⚪ 不在"),
    ]:
        group = [r for r in rows if r["status"] == status]
        lines = [f"*{label}：{len(group)}人*"]
        for r in group:
            suffix = ""
            if status == "PRESENT":
                suffix = f" {local(r['check_in_at'])}〜"
            elif status == "UNCONFIRMED":
                suffix = f" 最終確認 {local(r['last_confirmed_at'])}"
            lines.append(f"<@{r['slack_user_id']}>{suffix}")
        blocks.append(text("\n".join(lines)))
    blocks.append(actions(button("入室する", "check_in"), button("退室する", "check_out")))
    return blocks


def confirmation(notice):
    return [
        text(
            "*在室確認*\n入室時刻："
            + local(notice["check_in_at"])
            + "\n最終確認："
            + local(notice["last_confirmed_at"])
            + "\nまだ研究室にいますか？"
        ),
        actions(
            button("まだいる", "confirm_presence", notice["id"]),
            button("退室した", "confirm_checkout", notice["id"]),
            button("退室時刻を指定", "edit_checkout", f"n:{notice['id']}"),
        ),
    ]


def completed_confirmation(notice):
    if notice["status"] == "ANSWERED":
        message = "✅ 回答済み\n回答日時：" + local(notice["responded_at"])
    else:
        message = "この在室確認は終了しました。"
    return [text("*在室確認*\n" + message), text("最新の在室状況はApp Homeで確認できます。")]
