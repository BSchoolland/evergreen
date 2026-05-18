# Configure Evergreen Cron Server

Interactive setup for the evergreen cron server that runs scheduled skills.

## What to do

1. Show the current schedule by querying `~/.evergreen/evergreen.db`:

```sql
SELECT skill, interval_minutes, enabled, last_run_at FROM cron_jobs ORDER BY skill;
```

If the table is empty or doesn't exist, run `python3 scripts/evergreen-server.py` briefly to initialize defaults, then stop it (it will populate the `cron_jobs` table on startup).

2. Show the user the current config and explain each skill:
   - **check-bugs**: Scans TACOS database logs for errors, anomalies, and data issues
   - **hackernews-monitor**: Watches HN for security vulnerabilities relevant to the TACOS stack
   - **tacos-audit**: Runs `bun audit` on TACOS dependencies and assesses real risk
   - **triage**: Runs automatically after any of the above if new items were found (not independently scheduled)

3. Ask the user what they want to change. They can:
   - Enable/disable specific skills
   - Change the interval (in minutes) for any skill
   - The intervals are in minutes: 60 = hourly, 360 = every 6h, 1440 = daily

4. Apply changes with SQL:

```sql
UPDATE cron_jobs SET interval_minutes = ?, enabled = ? WHERE skill = ?;
```

5. Show the updated schedule and confirm.

## Server management

Tell the user how to manage the server:
- Start: `bash scripts/server.sh start`
- Stop: `bash scripts/server.sh stop`
- Status: `bash scripts/server.sh status`
- Logs: `tail -f ~/.evergreen/server.log`

The server checks every 60 seconds whether any skill is due, runs it via `claude -p`, and then runs triage if new bugs or security alerts were recorded. Triage is skipped if nothing new is in the database — it costs zero tokens when there's nothing to do.
