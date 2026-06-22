CREATE TABLE IF NOT EXISTS bugs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at INTEGER NOT NULL DEFAULT (cast(strftime('%s', 'now') as integer)),
  environment TEXT NOT NULL,
  error_pattern TEXT NOT NULL,
  source_query TEXT,
  occurrence_count INTEGER DEFAULT 1,
  first_seen_at INTEGER NOT NULL,
  last_seen_at INTEGER NOT NULL,
  severity TEXT NOT NULL,
  summary TEXT NOT NULL,
  probable_root_cause TEXT,
  verified_root_cause TEXT,
  verification_notes TEXT,
  verification_path TEXT,
  pr_url TEXT,
  pr_status TEXT CHECK(pr_status IN ('open', 'merged', 'closed')),
  discord_message_id TEXT,
  status TEXT NOT NULL DEFAULT 'new' CHECK(status IN ('new', 'verified', 'unverified', 'blocked', 'not_actionable', 'in_progress', 'backlog', 'action_needed', 'resolved', 'dismissed')),
  resolved_at INTEGER,
  disposition_reason TEXT,
  dismissed_by TEXT CHECK(dismissed_by IN ('verify', 'pr_closed', 'owner'))
);

CREATE TABLE IF NOT EXISTS security_alerts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at INTEGER NOT NULL DEFAULT (cast(strftime('%s', 'now') as integer)),
  source TEXT NOT NULL,
  source_url TEXT,
  article_url TEXT,
  cve TEXT,
  name TEXT,
  severity TEXT NOT NULL,
  affected_component TEXT,
  summary TEXT NOT NULL,
  impact_assessment TEXT,
  pr_url TEXT,
  pr_status TEXT CHECK(pr_status IN ('open', 'merged', 'closed')),
  discord_message_id TEXT,
  status TEXT NOT NULL DEFAULT 'new' CHECK(status IN ('new', 'not_affected', 'not_actionable', 'in_progress', 'backlog', 'action_needed', 'resolved', 'dismissed')),
  resolved_at INTEGER,
  disposition_reason TEXT,
  dismissed_by TEXT CHECK(dismissed_by IN ('verify', 'pr_closed', 'owner')),
  UNIQUE(source, cve, source_url)
);

CREATE TABLE IF NOT EXISTS discord_messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  discord_message_id TEXT UNIQUE,
  channel_id TEXT NOT NULL,
  direction TEXT NOT NULL CHECK(direction IN ('outbound', 'inbound')),
  author_id TEXT,
  content TEXT NOT NULL,
  reply_to_message_id TEXT,
  created_at INTEGER NOT NULL DEFAULT (cast(strftime('%s', 'now') as integer)),
  read_at INTEGER
);

CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at INTEGER NOT NULL DEFAULT (cast(strftime('%s', 'now') as integer)),
  finished_at INTEGER,
  type TEXT NOT NULL,
  summary TEXT,
  cost REAL,
  tokens INTEGER,
  model TEXT,
  effort TEXT,
  parent_run_id INTEGER REFERENCES runs(id) ON DELETE SET NULL,
  branch TEXT,
  pr_url TEXT
);

CREATE TABLE IF NOT EXISTS cron_jobs (
  skill TEXT PRIMARY KEY,
  interval_minutes INTEGER NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  last_run_at INTEGER
);

CREATE TABLE IF NOT EXISTS skill_queue (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  skill TEXT NOT NULL,
  queued_at INTEGER NOT NULL DEFAULT (cast(strftime('%s', 'now') as integer)),
  started_at INTEGER,
  status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'running', 'done')),
  source TEXT NOT NULL DEFAULT 'cron',
  force INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS configs (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

-- Interactive conversations: each Discord thread maps to one persistent pi session.
-- The "interactive dev" works in its own clone of the target repo (project_path_interactive);
-- the watchdog works in project_path. They share this DB but never share a working tree.
CREATE TABLE IF NOT EXISTS conversations (
  thread_id TEXT PRIMARY KEY,
  channel_id TEXT NOT NULL,
  session_id TEXT NOT NULL,
  session_file TEXT,
  status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'idle', 'closed')),
  seed_issue_type TEXT CHECK(seed_issue_type IN ('bug', 'security_alert')),
  seed_issue_id INTEGER,
  created_at INTEGER NOT NULL DEFAULT (cast(strftime('%s', 'now') as integer)),
  last_activity_at INTEGER NOT NULL DEFAULT (cast(strftime('%s', 'now') as integer))
);
