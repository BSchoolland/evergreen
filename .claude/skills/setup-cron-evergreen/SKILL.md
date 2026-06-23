# Configure Evergreen Cron Server

Interactive setup for the evergreen cron server that runs scheduled skills.

## What to do

1. Show the current schedule by querying `~/.evergreen/evergreen.db`:

```sql
SELECT skill, interval_minutes, enabled, last_run_at FROM cron_jobs ORDER BY skill;
```

If the table is empty or doesn't exist, run `python3 scripts/evergreen-server.py` briefly to initialize defaults, then stop it (it will populate the `cron_jobs` table on startup).

2. Show the user the current config and explain each skill:
   - **check-bugs**: Scans project database logs for errors, anomalies, and data issues
   - **hackernews-monitor**: Watches HN for security vulnerabilities relevant to the project's stack
   - **dependency-audit**: Runs dependency audit on the project and assesses real risk
   - **update-status**: Syncs PR statuses, processes Discord replies, resolves completed issues (every 4h, skips if nothing is in progress)
   - **reconcile**: Unsticks issues stranded in a status no skill will advance — returns them to the conveyor, or consolidates a class of unfixable bugs into a `themes` row (every 3 days)
   - **verify-bug**: Runs automatically after detection skills if new bugs were found. Attempts to reproduce and confirm the root cause before triage acts on it. (not independently scheduled)
   - **triage**: Runs automatically after verify-bug. Only opens PRs for verified bugs; unverified bugs get notify-only. Security alerts are triaged directly. (not independently scheduled)

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

The server checks every 60 seconds whether any skill is due, runs it via the configured engine, then runs verify-bug (if new bugs exist) followed by triage (if verified/unverified bugs or new security alerts exist). Both are skipped if there's nothing to process — they cost zero tokens when there's nothing to do.

## Engine (claude / pi / codex)

The engine is the single word in `~/.evergreen/config`: `claude` (default today),
`pi`, or `codex`. To run the watchdog on **pi** (unifying it with the interactive dev):

1. `echo pi > ~/.evergreen/config`
2. Optionally pin a model: set the `pi_model` config (e.g. `anthropic/claude-opus-4-5`),
   otherwise pi uses its own default model.
3. Restart: `bash scripts/server.sh stop && bash scripts/server.sh start`.

On pi the server regenerates `.pi/skills` from `.claude/skills` at startup (via
`scripts/sync-pi-skills.py`), invokes skills as `/skill:<name>-evergreen`, runs in the
evergreen repo with `EVERGREEN_PROJECT_PATH` pointed at the watchdog's clone
(`project_path`), and resumes the same session for the work-summary step. The
interactive dev (the Discord bot) always runs on pi regardless of this setting.
