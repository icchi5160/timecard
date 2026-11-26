CREATE TABLE users (
 slack_user_id TEXT PRIMARY KEY, display_name TEXT NOT NULL,
 is_active INTEGER NOT NULL CHECK(is_active IN (0,1)),
 is_admin INTEGER NOT NULL CHECK(is_admin IN (0,1)), created_at TEXT NOT NULL
);
CREATE TABLE sessions (
 id INTEGER PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(slack_user_id),
 check_in_at TEXT NOT NULL, check_out_at TEXT, last_confirmed_at TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('PRESENT','ABSENT','UNCONFIRMED')),
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 CHECK(check_out_at IS NULL OR check_out_at >= check_in_at),
 CHECK((check_out_at IS NULL AND status IN ('PRESENT','UNCONFIRMED'))
 OR (check_out_at IS NOT NULL AND status='ABSENT'))
);
CREATE UNIQUE INDEX idx_one_open_session ON sessions(user_id) WHERE check_out_at IS NULL;
CREATE INDEX idx_sessions_user_time ON sessions(user_id, check_in_at);
CREATE TABLE attendance_events (
 id INTEGER PRIMARY KEY, session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
 actor_user_id TEXT NOT NULL REFERENCES users(slack_user_id), event_type TEXT NOT NULL,
 occurred_at TEXT NOT NULL, metadata_json TEXT, idempotency_key TEXT UNIQUE
);
CREATE TABLE notifications (
 id INTEGER PRIMARY KEY, session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
 user_id TEXT NOT NULL REFERENCES users(slack_user_id), cycle_key TEXT NOT NULL,
 scheduled_at TEXT NOT NULL, sent_at TEXT, responded_at TEXT,
 status TEXT NOT NULL CHECK(status IN
 ('PENDING','SENDING','SENT','ANSWERED','EXPIRED','FAILED','CANCELLED')),
 slack_message_ts TEXT, attempt_count INTEGER NOT NULL DEFAULT 0,
 UNIQUE(session_id,cycle_key)
);
CREATE TABLE app_settings (key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE outbox (
 id INTEGER PRIMARY KEY, event_type TEXT NOT NULL, payload_json TEXT NOT NULL,
 created_at TEXT NOT NULL, processed_at TEXT, attempt_count INTEGER NOT NULL DEFAULT 0
);
INSERT INTO app_settings VALUES ('schema_version','1');
