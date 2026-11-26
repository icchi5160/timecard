CREATE TABLE operation_receipts (
 idempotency_key TEXT PRIMARY KEY,
 actor_user_id TEXT NOT NULL REFERENCES users(slack_user_id),
 session_id INTEGER REFERENCES sessions(id) ON DELETE SET NULL,
 created_at TEXT NOT NULL
);
UPDATE app_settings SET value='2' WHERE key='schema_version';
