Triage issues in the evergreen database (~/.evergreen/evergreen.db) across the `bugs` and `security_alerts` tables.

Use `/read-config-evergreen` to get the project path, project name, and owner name.

Scan triageable issues: `verified` bugs and `new` security alerts. Pick what to work on this run: either the single most important issue, or a batch of low-importance ones that can be handled together. Use judgment — if something serious is in the queue, skip the noise and focus on it. Anything not picked up will be caught on subsequent runs.

Also scan `unverified` bugs — these can only be handled as notify-only (no PRs). Batch-notify if there are several, or skip if they're low importance.

## Investigation (do this before any action)

For **verified bugs**: the verify-bug skill has already confirmed the root cause and written reproduction evidence to the `verification_path` folder. Read the verification `.md` file and POC scripts. Use `verified_root_cause` (not `probable_root_cause`) as the basis for your fix. You still need to:

- Read the relevant source code thoroughly. Understand the full context: callers, error handling paths, tests, related modules.
- Check recent commits and open/merged PRs in the repo for keywords from the bug's error pattern. If someone may have already fixed the issue since the last occurrence, note that in your assessment and do not open a duplicate PR — acknowledge it or notify with what you found instead.
- For code changes: read every function in the call chain that your change affects. Understand what happens upstream and downstream of your edit.
- If the issue has multiple root causes (e.g., code + config + infra), identify all of them. Don't fix one and hand-wave the rest.

For **security alerts**: verify independently as before — read source code, check if the project actually uses the affected component, assess real impact.

## Before opening a PR

- Create a fresh branch off of `origin/master` (fetch first) so the PR is clean and doesn't carry unrelated changes.
- **Every evergreen PR must carry the `evergreen` GitHub label** so the owner can spot at a glance that it came from evergreen. The label may not exist in the target repo yet, so ensure it exists first, then apply it when you open the PR:

  ```
  gh label create evergreen --color 0e8a16 --description "Opened by evergreen" 2>/dev/null || true
  gh pr create --label evergreen ...
  ```
- Verify the fix handles edge cases and doesn't break existing behavior.
- Read existing tests. Run them. Write new tests if the changed behavior isn't covered.
- Typecheck passing is necessary but not sufficient — think about runtime behavior.
- **Prove the fix**: re-run the reproduction from the verification folder after applying your fix. The PR must include before/after evidence showing the issue is resolved. If you can't demonstrate the fix works, don't open a PR — notify instead.
- If you're not confident the fix is correct, don't open a PR. Notify instead and explain what you found.

## PR descriptions

The owner gets multiple triage PRs per day and will close any they can't verify in 5–10 minutes. Every PR must include:

- **Reproduction steps**: concrete commands, URLs, or DB queries to see the issue firsthand.
- **Frequency and recency**: how often it's happening and when it last occurred.
- **Verification evidence**: reference the verification folder and summarize what verify-bug proved. Include the before/after results showing the fix resolves the issue.

If you can't provide a clear path for the owner to see the problem themselves, the PR isn't ready.

## After opening a PR

Run `/code-review high`. Evaluate each finding and fix any that are valid — push follow-up commits to the same branch. Re-run tests and typecheck after fixes. Repeat the review until it surfaces no new valid findings or you've done 3 review cycles.

**Do not send the PR-ready Discord notification until after the review cycle is complete.** The PR should be in good shape before you ping anyone *about the PR*. (Exception: an active-outage heads-up — see Notification judgment — goes out immediately when the outage is detected and is never delayed for the review or for PR prep. That is a separate message from the later "here's the PR" ping.)

## Action tiers

- **Acknowledge**: Not relevant to the project, informational-only. Mark status='not_actionable' (or 'not_affected' for security alerts about components the project doesn't use).
- **PR**: High-importance code or dependency fix. Create a branch, fix it, open a PR. Mark status='in_progress'.
- **PR + notify**: Only for very high importance — active production impact, data loss risk, security vulnerabilities, or rapidly escalating failures. Open PR and send Discord message. Mark status='in_progress'. Always include the last occurrence date/time in the notification.
- **Notify only**: Non-code issue (config, infra, DB) that is very high importance. Send Discord message with diagnosis and recommendation. Mark status='in_progress'. Always include the last occurrence date/time in the notification.

For truly critical issues (production/staging is actively down, etc) load the /triage-critical-evergreen skill.

## Notification judgment

**Active outage → notify immediately, before anything else.** The single most important trigger is: *is production actively failing right now, or did a real outage just fire?* An active-outage signature is a burst of real failures occurring in real time or within the current triage window (an ongoing or just-fired spike — e.g. hundreds of failure events in a short window that production is still inside of or only just exited), as opposed to a stale one-off. When you see this, send a Discord heads-up immediately with the diagnosis — *regardless of* whether a code fix/PR is also coming, and *do not* sit on it to prepare the PR first. The owner may need to take remediation into his own hands (top up billing, restart a service, roll back) while you work the fix. This is a separate axis from severity/recency: judge it by "actively failing," not by issue type. "Hasn't fired in days" ≠ active outage.

> Context: PR #324 (transcription OpenAI-quota retry storm) was opened silently as a routine code fix. The owner closed it: *"this WAS an active outage, LLM requests were actively failing… this should have been a discord message."* The miss was treating a live outage as ordinary PR work. See memory [[ai-inference-fixes-prefer-discord]].

For everything that is *not* an active outage, ask: "Would I message my boss about this outside of working hours?" If the answer is no, do not send a Discord notification. Most bugs — even real ones — can wait. A medium-severity bug that hasn't occurred in a week is not worth a ping. Reserve notifications for situations where delay would cause real harm: active outages, data loss, security breaches, or rapidly worsening failures.

Discord: use `/discord-evergreen` to notify the project owner. After sending, update the bug or security alert's `discord_message_id` column with the Discord message ID so replies can be tracked back to the issue.
