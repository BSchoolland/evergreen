# Check TACOS for Bugs

Query the TACOS databases on staging and production for the last 24 hours. Get connection strings from SSM (`aws ssm get-parameter --name "/tacos/{env}/DATABASE_URL" --with-decryption`). Staging is remote Postgres (query directly via psql). Production is local to the server (query via `ssh tacos-prod "sudo -u postgres psql -d tacos_production -c '...'"`).

Check all tables that could surface bugs: `events` (wide events — errors, slow requests, failed jobs), `error_logs`, `cost_events`, `billing_events`, `scrape_jobs` (stuck/failed), `credit_transactions` (balance anomalies), `sync_budget_state`, `conversations` (error status), `chat_messages`. Scope everything to the last 24 hours.

Use `\d table_name` before querying if unsure of column names — Prisma maps camelCase to snake_case inconsistently.

Your scope is strictly limited to:
- **Errors**: Failures, unhandled exceptions, jobs erroring out. Identify root cause if possible.
- **Logging gaps**: Fields that are null/empty when they shouldn't be, or missing event chains (e.g. job completion with no start).
- **Data anomalies**: Negative balances, stuck jobs, conversations permanently in error state.
- **Trend anomalies**: Compare today's numbers to the daily average from the past 7 days. Flag significant deviations in: request volume (total events by type), error rate, cost spend by category, conversation creation rate, scrape job volume/failure rate, and feature usage (group by path or business.action). A spike or drop worth flagging is roughly 2x or 0.5x the 7-day average — use judgment, don't be noisy about minor fluctuations.

Do NOT file for style preferences, potential improvements, or things working correctly. Only file for demonstrable bugs or incomplete log entries. It is totally fine to find nothing.

For each finding, cite specific log entry IDs/timestamps. Read the TACOS codebase at `/home/ben/Projects/TACOS` to confirm before recording. Record bugs with `python3 scripts/record_bug.py`.

Before recording new bugs, check existing open bugs (`python3 scripts/record_bug.py list --open`) and update occurrence counts rather than creating duplicates. If a previously recorded bug no longer appears in the logs, mark it resolved.
