Triage `new` issues in the evergreen database (~/.evergreen/evergreen.db) across the `bugs` and `security_alerts` tables.

Use `/read-config-evergreen` to get the project path, project name, and owner name.

Scan all new issues, then pick what to work on this run: either the single most important issue, or a batch of low-importance ones that can be handled together. Use judgment — if something serious is in the queue, skip the noise and focus on it. Anything not picked up will be caught on subsequent runs.

## Investigation (do this before any action)

Don't trust bug summaries or severity fields — verify independently. Before deciding on an action:

- Read the relevant source code thoroughly. Understand the full context: callers, error handling paths, tests, related modules.
- For bugs: check the actual logs/DB state to confirm the issue is still present and the described root cause is accurate.
- For code changes: read every function in the call chain that your change affects. Understand what happens upstream and downstream of your edit.
- If the issue has multiple root causes (e.g., code + config + infra), identify all of them. Don't fix one and hand-wave the rest.

## Before opening a PR

- Verify the fix handles edge cases and doesn't break existing behavior.
- Read existing tests. Run them. Write new tests if the changed behavior isn't covered.
- Typecheck passing is necessary but not sufficient — think about runtime behavior.
- If you're not confident the fix is correct, don't open a PR. Notify instead and explain what you found.

## After opening a PR

Poll for an automated review from Greptile (`gh api repos/OWNER/REPO/pulls/NUMBER/comments`) for up to 15 minutes. Once findings appear, evaluate each one and fix any that are valid — push follow-up commits to the same branch. Re-run tests and typecheck after fixes. Then comment `@greptileai please review again` on the PR and poll for the second review. Repeat until Greptile has no new findings or you've done 3 review cycles.

## Action tiers

- **Acknowledge**: Not relevant to the project, informational-only. Mark status='not_actionable' (or 'not_affected' for security alerts about components the project doesn't use).
- **PR**: Small code or dependency fix. Create a branch, fix it, open a PR. Mark status='in_progress'.
- **PR + notify**: Important code fix (production, recurring, or medium+ severity). Open PR and send Discord message. Mark status='in_progress'.
- **Notify only**: Non-code issue (config, infra, DB). Send Discord message with diagnosis and recommendation. Mark status='in_progress'.

For truly critical issues (production/staging is actively down, etc) load the /triage-critical-evergreen skill.

Discord: use `/discord-evergreen` to notify the project owner.
