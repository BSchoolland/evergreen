Handle a critical issue identified during triage. Only use this for situations where production or staging is actively down, or a critical security vulnerability is being actively exploited — where no reasonable argument could be made that we should wait.

Use `/read-config-evergreen` to get the project path, owner name, and SSH aliases. It also
resolves `EVERGREEN_PROJECT_ID` — the project you're scoped to. When set, scope every
`bugs` / `security_alerts` query and `runs` log to that project and pass
`--project-id "$EVERGREEN_PROJECT_ID"` to any record scripts; when unset, you're on project 1.

Process:

1. Investigate thoroughly. Read logs, source code, config — understand the issue fully before acting.
2. Send a Discord message (via `/discord-evergreen`) describing the issue, your diagnosis, and your proposed fix.
3. Wait 30 minutes for a response.
4. If no response and you are confident in the fix: act. You MUST have a rollback plan before making any change. Log every action taken to the `runs` table.
5. If unsure about the fix even after investigation: do not act. Send a follow-up Discord message and wait.

If your fix takes the form of a PR, it must carry the `evergreen` GitHub label like every other evergreen PR (see triage-evergreen) — create the label if it doesn't exist, then `gh pr create --label evergreen ...`.

Record all actions, reasoning, and rollback steps in the database. Err on the side of not acting.
