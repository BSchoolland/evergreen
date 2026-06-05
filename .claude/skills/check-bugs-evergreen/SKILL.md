# Check for Bugs

Use `/read-config-evergreen` to get the project path, SSH aliases, and project name.

Query the project's databases on staging and production for the last 24 hours. Discover connection strings from the project's config, environment variables, or secrets manager. Use `\d table_name` before querying to discover schema — don't assume table or column names.

Your scope is strictly limited to:
- **Errors**: Failures, unhandled exceptions, jobs erroring out. Identify probable root cause if possible.
- **Logging gaps**: Fields that are null/empty when they shouldn't be, or missing event chains (e.g. job completion with no start).
- **Data anomalies**: Negative balances, stuck jobs, conversations permanently in error state.
- **Trend anomalies**: Compare today's numbers to the daily average from the past 7 days. Flag significant deviations in: request volume (total events by type), error rate, cost spend by category, conversation creation rate, scrape job volume/failure rate, and feature usage (group by path or business.action). A spike or drop worth flagging is roughly 2x or 0.5x the 7-day average — use judgment, don't be noisy about minor fluctuations.

Do NOT file for style preferences, potential improvements, or things working correctly. Only file for demonstrable bugs or incomplete log entries. It is totally fine to find nothing.

For each finding, cite specific log entry IDs/timestamps. Read the project codebase to confirm before recording. Record bugs with `python3 scripts/record_bug.py`.

**Active outage → escalate immediately, don't just record it.** If a finding is an active-outage signature — a burst of real failures occurring in real time or still inside the last few hours of the query window (e.g. hundreds of failure events that production is still inside of or only just exited), not a stale one-off — send a Discord heads-up immediately (`/discord-evergreen`) with the diagnosis and last-occurrence time, *in addition to* recording it for the normal verify/triage flow. The owner may need to remediate (top up billing, restart, roll back) while the fix is worked. Don't wait for verify-bug or triage to run. See the triage skill's "Notification judgment" and memory note on this.

Before recording new bugs, check existing bugs (`python3 scripts/record_bug.py list --open`) and update occurrence counts rather than creating duplicates. If a previously recorded bug no longer appears in the logs, mark it resolved.

Also check for `dismissed` bugs: `python3 scripts/record_bug.py list --dismissed`. If a new finding matches the error_pattern of a dismissed bug, skip it — the owner has already reviewed and rejected it.
