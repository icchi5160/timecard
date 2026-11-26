from unittest.mock import Mock

import pytest
from slack_sdk.errors import SlackApiError

from timecard.config import Config
from timecard.domain.errors import DomainError
from timecard.slack.handlers import register, require_access
from timecard.slack.home import render
from timecard.slack.modals import checkout_modal, submitted_time
from timecard.slack.sync import SlackSync


@pytest.fixture
def sync(env):
    db, clock, attendance, stats, _ = env
    client = Mock()
    client.chat_postMessage.return_value = {"ts": "123.4"}
    client.conversations_open.return_value = {"channel": {"id": "D1"}}
    config = Config(team_id="T1", channel_id="C1")
    return SlackSync(db, client, config, attendance, stats, clock)


def test_outbox_retries_and_dashboard_reused(env, sync):
    db, clock, attendance, _, _ = env
    attendance.change("U1", "check_in", "in")
    sync.client.chat_postMessage.side_effect = RuntimeError("offline")
    sync.process()
    with db.connect() as conn:
        assert (
            conn.execute("SELECT count(*) FROM outbox WHERE processed_at IS NULL").fetchone()[0]
            == 3
        )
    sync.client.chat_postMessage.side_effect = None
    clock.advance(minutes=2)
    sync.process()
    assert sync.client.chat_update.called
    assert sync.client.views_publish.called
    with db.connect() as conn:
        assert (
            conn.execute("SELECT count(*) FROM outbox WHERE processed_at IS NULL").fetchone()[0]
            == 0
        )
    sync.client.chat_postMessage.reset_mock()
    sync.dashboard()
    sync.client.chat_postMessage.assert_not_called()
    sync.client.chat_update.assert_called()


def test_deleted_dashboard_recreated(env, sync):
    sync.dashboard()
    sync.client.chat_update.side_effect = SlackApiError("deleted", {"error": "message_not_found"})
    sync.dashboard()
    assert sync.client.chat_postMessage.call_count == 2


@pytest.mark.parametrize(
    "body",
    [
        {"team": {"id": "T2"}, "user": {"id": "U1"}},
        {"team": {"id": "T1"}, "user": {"id": "U3"}},
        {"team": {"id": "T1"}, "user": {"id": "U1"}, "channel": {"id": "C2"}},
    ],
)
def test_slack_access_rejected(env, sync, body):
    db, _, _, _, _ = env
    with pytest.raises(DomainError):
        require_access(body, sync.config, db)


def test_home_only_contains_own_history(env):
    _, clock, attendance, stats, _ = env
    attendance.change("U2", "check_in", "other")
    attendance.change("U1", "check_in", "mine")
    clock.advance(hours=1)
    view = render(attendance, stats, "U1")
    assert "暫定" in str(view)
    assert "確定" in str(view)
    assert "U2" not in str(view)


class FakeApp:
    def __init__(self):
        self.callbacks = {}

    def decorator(self, name):
        def register_callback(fn):
            self.callbacks[fn.__name__] = fn
            return fn

        return register_callback

    action = decorator
    event = decorator
    view = decorator


def callbacks(env, sync):
    db, _, attendance, _, _ = env
    app = FakeApp()
    register(app, sync.config, db, attendance, sync)
    return app.callbacks


def test_button_handler_ack_and_idempotency(env, sync):
    _, _, attendance, _, _ = env
    handlers = callbacks(env, sync)
    body = {
        "team": {"id": "T1"},
        "user": {"id": "U1"},
        "channel": {"id": "C1"},
        "actions": [{"action_id": "timecard_check_in", "value": "main", "action_ts": "1"}],
    }
    ack = Mock()
    handlers["handle_action"](ack, body, sync.client)
    handlers["handle_action"](ack, body, sync.client)
    assert ack.call_count == 2
    assert len(attendance.history("U1")) == 1
    sync.client.chat_postEphemeral.assert_not_called()


def test_modal_validation_and_submission(env, sync):
    _, clock, attendance, _, _ = env
    sid = attendance.change("U1", "check_in", "in")
    clock.advance(hours=2)
    attendance.change("U1", "check_out", "out")
    metadata = {"action": "correct", "session_id": sid, "user": "U1"}
    view = checkout_modal(metadata, clock())
    view["id"] = "V1"
    view["state"] = {
        "values": {
            "date": {"date": {"selected_date": "2026-01-05"}},
            "time": {"time": {"selected_time": "10:00"}},
        }
    }
    assert submitted_time(view).hour == 10
    ack = Mock()
    callbacks(env, sync)["handle_checkout"](ack, {"team": {"id": "T1"}, "user": {"id": "U1"}}, view)
    ack.assert_called_once_with()
    assert attendance.history("U1")[0]["check_out_at"].startswith("2026-01-05T01")


def test_modal_other_owner_rejected(env, sync):
    _, clock, attendance, _, _ = env
    sid = attendance.change("U1", "check_in", "in")
    clock.advance(hours=1)
    attendance.change("U1", "check_out", "out")
    view = checkout_modal({"action": "correct", "session_id": sid, "user": "U1"}, clock())
    view["id"] = "V1"
    ack = Mock()
    callbacks(env, sync)["handle_checkout"](ack, {"team": {"id": "T1"}, "user": {"id": "U2"}}, view)
    assert ack.call_args.kwargs["response_action"] == "errors"


@pytest.mark.parametrize("response", ["confirm", "check_out", "home_confirm", "dashboard_checkout"])
def test_notification_buttons_removed_after_response(env, sync, response):
    from timecard.services.notifications import NotificationService

    db, clock, attendance, _, _ = env
    attendance.change("U1", "check_in", "in")
    clock.advance(hours=6)
    notifications = NotificationService(db, sync.send_confirmation, clock=clock)
    notifications.tick()
    sync.process()
    sync.client.chat_update.reset_mock()
    with db.connect() as conn:
        notice = conn.execute("SELECT * FROM notifications").fetchone()
    action = "confirm" if response in ("confirm", "home_confirm") else "check_out"
    notification_id = notice["id"] if response in ("confirm", "check_out") else None
    attendance.change("U1", action, "response", notification_id=notification_id)
    sync.process()
    updates = [
        call.kwargs
        for call in sync.client.chat_update.call_args_list
        if call.kwargs["channel"] == "D1"
    ]
    assert len(updates) == 1
    assert updates[0]["ts"] == "123.4"
    assert all(block["type"] != "actions" for block in updates[0]["blocks"])
    expected = "回答済み" if notification_id else "終了"
    assert expected in str(updates[0]["blocks"])


def test_notification_button_cleanup_failure_retries_after_restart(env, sync):
    from timecard.services.notifications import NotificationService

    db, clock, attendance, stats, _ = env
    attendance.change("U1", "check_in", "in")
    clock.advance(hours=6)
    NotificationService(db, sync.send_confirmation, clock=clock).tick()
    sync.process()
    attendance.change("U1", "confirm", "reply", notification_id=1)

    def fail_dm_update(**kwargs):
        if kwargs["channel"] == "D1":
            raise RuntimeError("offline")

    sync.client.chat_update.side_effect = fail_dm_update
    sync.process()
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM outbox WHERE event_type='NOTIFICATION_UPDATE'").fetchone()
        assert row["processed_at"] is None
        assert row["attempt_count"] == 1
    clock.advance(minutes=2)
    sync.client.chat_update.side_effect = None
    restarted = SlackSync(db, sync.client, sync.config, attendance, stats, clock)
    restarted.process()
    with db.connect() as conn:
        assert (
            conn.execute(
                "SELECT processed_at FROM outbox WHERE event_type='NOTIFICATION_UPDATE'"
            ).fetchone()[0]
            is not None
        )
    assert any(c.kwargs["channel"] == "D1" for c in sync.client.chat_update.call_args_list)


def test_unconfirmed_notification_keeps_response_buttons(env, sync):
    from timecard.services.notifications import NotificationService

    _, clock, attendance, _, _ = env
    attendance.change("U1", "check_in", "in")
    clock.advance(hours=6)
    job = NotificationService(sync.database, sync.send_confirmation, clock=clock)
    job.tick()
    sync.process()
    sync.client.chat_update.reset_mock()
    clock.advance(minutes=30)
    job.tick()
    sync.process()
    assert attendance.current("U1")["status"] == "UNCONFIRMED"
    assert not any(c.kwargs["channel"] == "D1" for c in sync.client.chat_update.call_args_list)


def home_action_ids(view):
    return {
        element["action_id"]
        for block in view["blocks"]
        if block["type"] == "actions"
        for element in block["elements"]
    }


@pytest.mark.parametrize("state", ["ABSENT", "PRESENT", "UNCONFIRMED"])
def test_home_only_offers_actions_for_current_state(env, state):
    from timecard.services.notifications import NotificationService

    db, clock, attendance, stats, _ = env
    if state != "ABSENT":
        attendance.change("U1", "check_in", "in")
    if state == "UNCONFIRMED":
        clock.advance(hours=6)
        notifications = NotificationService(db, Mock(return_value="dm-ts"), clock=clock)
        notifications.tick()
        clock.advance(minutes=30)
        notifications.tick()
    ids = home_action_ids(render(attendance, stats, "U1"))
    if state == "ABSENT":
        assert ids == {"timecard_check_in"}
    else:
        assert ids == {"timecard_check_out", "timecard_confirm_presence"}


@pytest.mark.parametrize("state", ["PRESENT", "UNCONFIRMED"])
def test_stale_home_check_in_refreshes_controls_without_new_session(env, sync, state):
    from timecard.services.notifications import NotificationService

    db, clock, attendance, _, _ = env
    attendance.change("U1", "check_in", "in")
    if state == "UNCONFIRMED":
        clock.advance(hours=6)
        job = NotificationService(db, Mock(return_value="ts"), clock=clock)
        job.tick()
        clock.advance(minutes=30)
        job.tick()
    before = attendance.current("U1")
    body = {
        "team": {"id": "T1"},
        "user": {"id": "U1"},
        "view": {"type": "home"},
        "actions": [{"action_id": "timecard_check_in", "value": "main", "action_ts": "stale"}],
    }
    callbacks(env, sync)["handle_action"](Mock(), body, sync.client)
    view = sync.client.views_publish.call_args.kwargs["view"]
    assert home_action_ids(view) == {"timecard_check_out", "timecard_confirm_presence"}
    assert ("未確認" if state == "UNCONFIRMED" else "既に在室中") in str(view)
    assert attendance.current("U1") == before
    assert len(attendance.history("U1")) == 1


def test_home_controls_switch_after_check_in_and_out(env, sync):
    _, clock, attendance, _, _ = env
    handlers = callbacks(env, sync)
    body = {
        "team": {"id": "T1"},
        "user": {"id": "U1"},
        "view": {"type": "home"},
        "actions": [{"action_id": "timecard_check_in", "value": "main", "action_ts": "1"}],
    }
    handlers["handle_action"](Mock(), body, sync.client)
    assert home_action_ids(sync.client.views_publish.call_args.kwargs["view"]) == {
        "timecard_check_out",
        "timecard_confirm_presence",
    }
    clock.advance(minutes=1)
    body["actions"] = [{"action_id": "timecard_check_out", "value": "main", "action_ts": "2"}]
    handlers["handle_action"](Mock(), body, sync.client)
    ids = home_action_ids(sync.client.views_publish.call_args.kwargs["view"])
    assert "timecard_check_in" in ids
    assert "timecard_check_out" not in ids
    assert "timecard_confirm_presence" not in ids
    assert attendance.current("U1") is None
