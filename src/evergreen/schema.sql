-- Evergreen schema.
--
-- Two-tier model:
--   * INSTANCE  = this whole DB / state dir ($EVERGREEN_HOME). Hard isolation
--                 boundary (own Discord bot, own credentials). Never shares data
--                 with another instance.
--   * PROJECT   = a row in `projects`. An instance watches many projects. Almost
--                 everything (bugs, alerts, runs, uptime) is keyed by project_id.
--
-- Two monitoring planes:
--   * MONITORING plane = deterministic code crons (uptime/SSL). Writes
--                        uptime_checks / project_status. ~free, every project.
--   * AGENT plane      = judgment skills (check-bugs, triage, ...). Costs money,
--                        scheduled per (project, skill) in cron_jobs, depth-tiered.

CREATE TABLE IF NOT EXISTS projects (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  slug TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  -- code_db: deep log/PR monitoring (TACOS-style). wordpress: alert-only WP
  -- checks. static: uptime/SSL only.
  type TEXT NOT NULL DEFAULT 'static' CHECK(type IN ('code_db', 'wordpress', 'static')),
  status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'paused', 'archived')),
  base_url TEXT,
  -- Named depth preset (lite/standard/deep). Seeds cron_jobs intervals/models;
  -- cron_jobs is the live source of truth after that (tunable per project).
  depth_tier TEXT NOT NULL DEFAULT 'standard',
  -- JSON blob of per-project, type-specific config: project_path, ssh_staging,
  -- ssh_prod, wp_ssh, wp_path, alert_channel_id, uptime_enabled, etc. Kept as
  -- JSON (not normalized rows) so new project types add keys with no migration.
  config TEXT NOT NULL DEFAULT '{}',
  created_at INTEGER NOT NULL DEFAULT (cast(strftime('%s', 'now') as integer))
);

CREATE TABLE IF NOT EXISTS bugs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER NOT NULL DEFAULT 1,
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
  dismissed_by TEXT CHECK(dismissed_by IN ('verify', 'pr_closed', 'owner')),
  -- Optional link to the recurring theme (design flaw / tech debt) this bug is a symptom of.
  theme_id INTEGER REFERENCES themes(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS security_alerts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER NOT NULL DEFAULT 1,
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
  UNIQUE(project_id, source, cve, source_url)
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
  project_id INTEGER NOT NULL DEFAULT 1,
  started_at INTEGER NOT NULL DEFAULT (cast(strftime('%s', 'now') as integer)),
  finished_at INTEGER,
  type TEXT NOT NULL,
  summary TEXT,
  cost REAL,
  tokens INTEGER,
  input_tokens INTEGER,
  output_tokens INTEGER,
  model TEXT,
  effort TEXT,
  parent_run_id INTEGER REFERENCES runs(id) ON DELETE SET NULL,
  branch TEXT,
  pr_url TEXT
);

-- Agent-plane schedule, per (project, skill). depth_tier seeds these rows;
-- model/effort NULL = inherit the engine's per-skill code defaults.
CREATE TABLE IF NOT EXISTS cron_jobs (
  project_id INTEGER NOT NULL DEFAULT 1,
  skill TEXT NOT NULL,
  interval_minutes INTEGER NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  last_run_at INTEGER,
  model TEXT,
  effort TEXT,
  PRIMARY KEY (project_id, skill)
);

CREATE TABLE IF NOT EXISTS skill_queue (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER NOT NULL DEFAULT 1,
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

-- Monitoring plane: one row per uptime probe (HTTP + TLS), per project.
CREATE TABLE IF NOT EXISTS uptime_checks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  checked_at INTEGER NOT NULL DEFAULT (cast(strftime('%s', 'now') as integer)),
  is_up INTEGER NOT NULL,
  status_code INTEGER,
  response_time_ms INTEGER,
  ssl_expiry_days INTEGER,
  error TEXT
);
CREATE INDEX IF NOT EXISTS idx_uptime_project_time ON uptime_checks(project_id, checked_at);

-- Current up/down state per project, plus alert debounce bookkeeping.
CREATE TABLE IF NOT EXISTS project_status (
  project_id INTEGER PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
  is_up INTEGER,
  consecutive_failures INTEGER NOT NULL DEFAULT 0,
  last_status_code INTEGER,
  last_checked_at INTEGER,
  last_response_time_ms INTEGER,
  ssl_expiry_days INTEGER,
  last_alert_sent_at INTEGER,
  last_transition_at INTEGER
);

-- Notional model pricing. Cost is computed from tokens x these rates, NOT from
-- what the provider actually billed — so subscription/free models still carry a
-- comparable weight in the budget. Tune freely; values are dollars per 1M tokens.
CREATE TABLE IF NOT EXISTS model_pricing (
  model TEXT PRIMARY KEY,
  input_per_mtok REAL NOT NULL DEFAULT 0,
  output_per_mtok REAL NOT NULL DEFAULT 0,
  updated_at INTEGER NOT NULL DEFAULT (cast(strftime('%s', 'now') as integer))
);

-- Interactive conversations: each Discord thread maps to one persistent agent
-- session, scoped to one project's repo clone.
CREATE TABLE IF NOT EXISTS conversations (
  thread_id TEXT PRIMARY KEY,
  project_id INTEGER NOT NULL DEFAULT 1,
  channel_id TEXT NOT NULL,
  session_id TEXT NOT NULL,
  session_file TEXT,
  status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'idle', 'closed')),
  seed_issue_type TEXT CHECK(seed_issue_type IN ('bug', 'security_alert')),
  seed_issue_id INTEGER,
  created_at INTEGER NOT NULL DEFAULT (cast(strftime('%s', 'now') as integer)),
  last_activity_at INTEGER NOT NULL DEFAULT (cast(strftime('%s', 'now') as integer))
);

-- Themes: recurring areas of the codebase that cause consistent problems.
-- Distinct from a bug (a discrete incident): a theme is a root design cause that
-- spawns a *class* of bugs. It is long-lived, demands a "worth it?" judgment rather
-- than a quick patch, and is surfaced on the dashboard's Tech Debt tab. Bugs that
-- are symptoms point back via bugs.theme_id. Themes are authored by cross-bug
-- analysis (clustering verified root causes by subsystem), not by log watching.
CREATE TABLE IF NOT EXISTS themes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER NOT NULL DEFAULT 1,
  created_at INTEGER NOT NULL DEFAULT (cast(strftime('%s', 'now') as integer)),
  updated_at INTEGER NOT NULL DEFAULT (cast(strftime('%s', 'now') as integer)),
  title TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'design_flaw' CHECK(kind IN ('design_flaw', 'tech_debt', 'recurring_failure')),
  subsystem TEXT NOT NULL,           -- the codebase area, e.g. "transcription pipeline"
  summary TEXT NOT NULL,             -- the design analysis: what is wrong and why
  evidence_paths TEXT,               -- grounding file:line anchors (newline-separated)
  proposed_change TEXT,              -- the refactor that would resolve it
  effort TEXT CHECK(effort IN ('S', 'M', 'L', 'XL')),
  impact TEXT,                       -- what it costs to leave it unfixed
  worth_it_verdict TEXT CHECK(worth_it_verdict IN ('worth', 'phased', 'not_worth')),
  worth_it_reason TEXT,
  status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open', 'acknowledged', 'planned', 'in_progress', 'resolved', 'wont_fix')),
  disposition_reason TEXT,
  pr_url TEXT
);
