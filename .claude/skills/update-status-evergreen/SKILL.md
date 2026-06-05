# Update Issue Status

Use `/read-config-evergreen` to get the project path, project name, owner name, and discord channel ID.

Sync the status of all `in_progress` bugs and security alerts based on their linked PRs and Discord replies.

## 1. Ensure the Discord bot is running

Check with `pgrep -f "node.*services/discord-bot/index.js"`. If not running, start it:

```bash
cd services/discord-bot && nohup node index.js > /tmp/discord-bot.log 2>&1 &
```

Wait a few seconds for it to connect and run its first reply poll.

## 2. Sync PR statuses from GitHub

For every bug and security alert that has a `pr_url`, fetch the current PR state from GitHub:

```bash
gh pr view <pr_url> --json state --jq '.state'
```

Update `pr_status` in the database to match (`open`, `merged`, `closed`).

## 3. Update issue status based on PR state

- **PR merged**: Mark the issue `resolved` and set `resolved_at` to now.
- **PR closed (not merged)**: The owner reviewed and rejected the fix. Mark the issue `dismissed` — this means "not a real issue" or "not worth fixing." Do not clear the PR link or reopen the issue.
- **PR open**: Leave as `in_progress`. Optionally check for review comments or CI failures and note them in the summary.

## 4. Check for Discord replies

Query for unread inbound replies linked to issue notifications:

```sql
SELECT dm.id, dm.content, dm.reply_to_message_id, dm.created_at
FROM discord_messages dm
WHERE dm.direction = 'inbound' AND dm.read_at IS NULL
ORDER BY dm.created_at ASC;
```

For each reply, find the bug(s) or security alert(s) with a matching `discord_message_id`. The reply content is an instruction from the project owner — act on it:

When the owner says "issue" or "create an issue", they generally mean a row in the evergreen database (`bugs` or `security_alerts` table), not a GitHub issue, unless otherwise specified.

- If the reply gives fix guidance, update the issue's `probable_root_cause` field or note it.
- If the reply asks to create/update an issue, create or update the relevant row in the `bugs` or `security_alerts` table.
- If the reply says to close, ignore, or dismiss, mark the issue `dismissed`.
- If the reply is ambiguous, leave it for the next triage run and don't mark it read.

After acting on a reply, **acknowledge it** by sending a short Discord response via `/discord-evergreen` confirming what you did (e.g., "Done — updated bugs #1 and #2 with tracing guidance, will work on logging improvements."). Use `--no-wait` since you don't need to wait for a reply to the acknowledgment.

Then mark the original reply as read:

```sql
UPDATE discord_messages SET read_at = cast(strftime('%s', 'now') as integer) WHERE id = <id>;
```

## 5. Flag stale issues

Find `in_progress` issues with no PR and no recent Discord reply that have been in progress for more than 3 days. Report these as needing attention but don't change their status.

## 6. Summary

Print a summary of all actions taken: issues resolved, replies acted on, stale issues flagged. Keep it concise.
