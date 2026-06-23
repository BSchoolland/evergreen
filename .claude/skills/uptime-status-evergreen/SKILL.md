# Uptime Status

Read-only view of the monitoring plane for the current project. The code crons do
the actual polling and write `uptime_checks` / `project_status`; this skill only
**reads** that data so you can reason about whether a project is healthy. You do not
poll anything here.

Use `/read-config-evergreen` to resolve `EVERGREEN_PROJECT_ID` and the DB path. Scope
to the current project; if `EVERGREEN_PROJECT_ID` is unset, default to project 1.

```sh
DB="${EVERGREEN_HOME:-$HOME/.evergreen}/evergreen.db"
PID="${EVERGREEN_PROJECT_ID:-1}"
```

## Current state

`project_status` holds the live up/down state and debounce bookkeeping:

```sh
sqlite3 -header "$DB" "
  SELECT is_up, consecutive_failures, last_status_code,
         last_response_time_ms, ssl_expiry_days,
         datetime(last_checked_at,'unixepoch') AS last_checked,
         datetime(last_transition_at,'unixepoch') AS last_transition,
         datetime(last_alert_sent_at,'unixepoch') AS last_alert
  FROM project_status WHERE project_id=$PID;"
```

## Recent history

```sh
sqlite3 -header "$DB" "
  SELECT datetime(checked_at,'unixepoch') AS checked, is_up, status_code,
         response_time_ms, ssl_expiry_days, error
  FROM uptime_checks WHERE project_id=$PID
  ORDER BY checked_at DESC LIMIT 30;"
```

## Report

Summarize concisely:
- **up / down** (from `project_status.is_up`) and `consecutive_failures`.
- Last `status_code`, `response_time_ms`, and `ssl_expiry_days` (flag SSL expiring soon).
- When state last transitioned, and whether an alert was already sent (`last_alert_sent_at`)
  so you don't double-notify — alerting is the cron's job, not this skill's.
- Any pattern in recent history (flapping, climbing response times, recurring `error`).

If there are no rows for the project, say so — it likely means uptime polling isn't
enabled for this project yet, not that it's down.
