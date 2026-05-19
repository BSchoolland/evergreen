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
  root_cause TEXT,
  pr_url TEXT,
  pr_status TEXT CHECK(pr_status IN ('open', 'merged', 'closed')),
  discord_message_id TEXT,
  status TEXT NOT NULL DEFAULT 'new' CHECK(status IN ('new', 'not_actionable', 'in_progress', 'resolved')),
  resolved_at INTEGER
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
  status TEXT NOT NULL DEFAULT 'new' CHECK(status IN ('new', 'not_affected', 'not_actionable', 'in_progress', 'resolved')),
  resolved_at INTEGER,
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
  summary TEXT
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
  status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'running', 'done'))
);

CREATE TABLE IF NOT EXISTS configs (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
