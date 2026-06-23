# Check for Bugs

Use `/read-config-evergreen` to get the project path, SSH aliases, and project name. It also
resolves `EVERGREEN_PROJECT_ID` — the project you're scoped to this run. When it's set, pass
`--project-id "$EVERGREEN_PROJECT_ID"` to `record_bug.py add`; `record_bug.py list` already
scopes to that project automatically. When it's unset, omit the flag — the scripts default to
project 1 (TACOS) and behave exactly as before.

Examine the project's data sources for the last 24 hours. `/read-config-evergreen` returns the project's **data note** — where the signal actually lives and how to reach it (a database, application/server logs via pm2 / journalctl / docker, a log service, …). Follow it, and discover specifics at runtime rather than assuming structure (`\d` before querying a DB; tail the right logs for a server). If a project has no real data source beyond uptime, there's nothing to check here — that's fine.

Your scope is strictly limited to:
- **Errors**: Failures, unhandled exceptions, jobs erroring out. Identify probable root cause if possible.
- **Logging gaps**: Fields that are null/empty when they shouldn't be, or missing event chains (e.g. job completion with no start).
- **Data anomalies**: Negative balances, stuck jobs, conversations permanently in error state.
- **Trend anomalies**: Compare today against the 7-day daily average for whatever this project exposes (request/error volume, throughput, spend, feature usage). A spike or drop of roughly 2x or 0.5x is worth flagging — use judgment, don't be noisy about minor fluctuations.

Do NOT file for style preferences, potential improvements, or things working correctly. Only file for demonstrable bugs or incomplete log entries. It is totally fine to find nothing.

For each finding, cite specific log entry IDs/timestamps. Read the project codebase to confirm before recording. Record bugs with `python3 scripts/record_bug.py` (add `--project-id "$EVERGREEN_PROJECT_ID"` when scoped to a project).

Once you've localized a finding to specific code, check that code's recent git history in the project repo (`git log`/`git log -p` on the relevant files, plus open PRs via `gh pr list`). A commit or deploy near the bug's first or last occurrence often explains it — a change that introduced it, or one that already fixed it (in which case mark it resolved rather than recording). A recent commit or open PR touching that code may mean someone is already on it — note that on the bug instead of treating it as new.

Record an active outage or ongoing harm (e.g. a runaway loop burning API spend) as **high** severity so verify-bug picks it up first.

Before recording new bugs, check existing bugs (`python3 scripts/record_bug.py list --open`) and update occurrence counts rather than creating duplicates. If a previously recorded bug no longer appears in the logs, mark it resolved.

Also check for `dismissed` bugs: `python3 scripts/record_bug.py list --dismissed`. If a new finding matches the error_pattern of a dismissed bug, skip it — the owner has already reviewed and rejected it.
