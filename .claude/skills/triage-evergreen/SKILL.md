Triage issues in the evergreen database (`${EVERGREEN_HOME:-$HOME/.evergreen}/evergreen.db`) across the `bugs` and `security_alerts` tables.

Use `/read-config-evergreen` to get the project path, project name, and owner name. It also
resolves `EVERGREEN_PROJECT_ID` — the project you're scoped to this run. When it's set, every
`bugs` / `security_alerts` query you run must filter `project_id = $EVERGREEN_PROJECT_ID`, and
any `record_bug.py` / `record_alert.py` call must pass `--project-id "$EVERGREEN_PROJECT_ID"`
(`record_bug.py list` scopes automatically). When it's unset, don't filter — you're operating
on project 1 (TACOS), unchanged.

Scan triageable issues: `verified` and `action_needed` bugs, and `new` security alerts. Pick what to work on this run: either the single most important issue, or a batch of low-importance ones that can be handled together. Use judgment — if something serious is in the queue, skip the noise and focus on it. Anything not picked up will be caught on subsequent runs.

Also scan `unverified` bugs — these can only be handled as notify-only (no PRs). Batch-notify if there are several, or skip if they're low importance.

## Investigation (do this before any action)

For **verified bugs**: the verify-bug skill has already confirmed the root cause and written reproduction evidence to the `verification_path` folder. Read the verification `.md` file and POC scripts. Use `verified_root_cause` (not `probable_root_cause`) as the basis for your fix. You still need to:

- Read the relevant source code thoroughly. Understand the full context: callers, error handling paths, tests, related modules.
- Check recent commits and open/merged PRs in the repo for keywords from the bug's error pattern. If someone may have already fixed the issue since the last occurrence, note that in your assessment and do not open a duplicate PR — acknowledge it or notify with what you found instead.
- For code changes: read every function in the call chain that your change affects. Understand what happens upstream and downstream of your edit.
- If the issue has multiple root causes (e.g., code + config + infra), identify all of them. Fix only the ones you've actually confirmed; for any cause you can't prove, say so and leave it rather than shipping a speculative fix.

For **action_needed bugs**: the owner asked for a specific follow-up, recorded in `disposition_reason`. If it came from a closed PR (`pr_url`), read that PR first — its diff, the owner's close comment, and any review findings — to see what was rejected and why. Then do what they asked, honoring their guidance whether it's a fix to build or a pitfall to avoid. That's usually a new PR with the smallest fix that respects it, but follow `disposition_reason` if they wanted something else.

For **security alerts**: verify independently as before — read source code, check if the project actually uses the affected component, assess real impact.

Prefer the smallest change that fixes what you've proven. If the evidence shows only *that* something failed but not reliably *why* (the cause is gone from logs, or one example is standing in for a class), make the next occurrence diagnosable instead of guessing — a minimal observability/reporting change you can be sure of beats a larger behavioral fix you're hoping is right.

Before writing a *behavioral* fix, state the causal chain in one sentence: *`<cause at file:line>` causes `<symptom>`; `<this change>` fixes it because `<mechanism>`.* If you can't complete it with a real mechanism, don't ship a speculative behavioral change — notify, or open a small observability/logging PR so the next occurrence is diagnosable.

## Before opening a PR

Read [merge-patterns.md](merge-patterns.md) — an analysis of past evergreen PRs and why the owner merged or closed each. It distills what separates merged from rejected PRs (root cause at the right layer, smallest sufficient fix, proven-live bug, additive field semantics, code-PR vs. Discord-alert). Use it to sanity-check your plan before you build.

- Create a fresh branch off of `origin/master` (fetch first) so the PR is clean and doesn't carry unrelated changes.
- **Every evergreen PR must carry the `evergreen` GitHub label** so the owner can spot at a glance that it came from evergreen. The label may not exist in the target repo yet, so ensure it exists first, then apply it when you open the PR:

  ```
  gh label create evergreen --color 0e8a16 --description "Opened by evergreen" 2>/dev/null || true
  gh pr create --label evergreen ...
  ```
- Verify the fix handles edge cases and doesn't break existing behavior.
- Read existing tests. Run them. Write new tests if the changed behavior isn't covered.
- Typecheck passing is necessary but not sufficient — think about runtime behavior.
- **Prove the fix**: re-run the actual reproduction from the verification folder and show real before/after evidence the failing records now succeed. Mocked tests that assert your new code path is *called* don't count — if you can't re-run the real reproduction, notify instead of opening a PR.
- If you're not confident the fix is correct, don't open a PR. Notify instead and explain what you found.

## PR descriptions

The owner gets multiple triage PRs per day and will close any he can't verify in 5–10 minutes. Write the body the way an engineer explains a bug to a colleague: open with the problem — the symptom, its impact, and the root cause shown at `file:line` with the offending code — then the fix and the insight behind it. Lead with what's wrong, not a changelog of what you changed.

Every PR must include:

- **Reproduction** the owner can run on his own machine — a query or command against the live system, with the real failing data inline. Don't send him to files in evergreen's verification folder; he can't reach this machine.
- **Frequency and recency**: how often it's happening and when it last occurred.
- **Before/after evidence** that the fix resolves it.

If you can't give him a clear path to see the problem firsthand, the PR isn't ready.

## After opening a PR

Review the branch: `python3 scripts/review-branch.py --dir <project path> --branch <your branch>` (run from the evergreen repo). It runs pi review agents over the diff vs the repo's default branch (`origin/HEAD`) and prints their findings. Evaluate each, fix any that are valid — push follow-up commits to the same branch — then re-run tests and typecheck. Repeat until the review surfaces no new valid findings or you've done 3 cycles.

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
