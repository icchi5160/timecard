from timecard.domain.models import parse
from timecard.slack.blocks import actions, button, duration, local, text


def render(attendance, statistics, user, page=0, notice=None):
    current = attendance.current(user)
    blocks = [text("*timecard — 自分の記録*")]
    if notice:
        blocks.append(text(notice))
    if current:
        label = "在室" if current["status"] == "PRESENT" else "未確認・要確認"
        blocks.append(
            text(
                f"*{label}*\n入室：{local(current['check_in_at'])}\n"
                f"最終確認：{local(current['last_confirmed_at'])}"
            )
        )
        elapsed = (attendance.clock() - parse(current["check_in_at"])).total_seconds()
        blocks.append(text(f"現在のセッション：{duration(elapsed)}（暫定・未確定）"))
        blocks.append(
            actions(button("まだいる", "confirm_presence", "home"), button("退室する", "check_out"))
        )
    else:
        blocks.append(text("⚪ 不在"))
        blocks.append(actions(button("入室する", "check_in")))
    for label, seconds in statistics.summary(user).items():
        blocks.append(text(f"*{label}*　確定：{duration(seconds)}"))
    blocks.append(text(f"*最近のセッション（{page + 1}ページ）*"))
    history = attendance.history(user, page, size=10)
    for row in history:
        end = local(row["check_out_at"]) if row["check_out_at"] else "継続中（暫定）"
        blocks.append(text(f"{local(row['check_in_at'])} 〜 {end}"))
        if row["check_out_at"]:
            blocks.append(actions(button("退室時刻を修正", "edit_checkout", f"s:{row['id']}")))
    nav = []
    if page:
        nav.append(button("前へ", "open_history", page - 1))
    if len(history) == 10:
        nav.append(button("次へ", "open_history", page + 1))
    if nav:
        blocks.append(actions(*nav))
    return {"type": "home", "blocks": blocks}
