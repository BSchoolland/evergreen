# Reconcile Stuck Issues

Use `/read-config-evergreen` for the project path and owner name. This skill is
scoped to the current project (`EVERGREEN_PROJECT_ID`) — only ever touch rows for
that project. The DB is at `${EVERGREEN_HOME:-$HOME/.evergreen}/evergreen.db`.

Evergreen advances issues through a pipeline of skills (verify, triage, update-status, …), each acting on issues in a specific status. Some get stranded in a status no skill will ever advance. Find them — filtering `bugs`/`security_alerts` by `project_id = $EVERGREEN_PROJECT_ID` — and get them moving again. For each, do one of:

- **Put it back on the conveyor**: reset it to the earliest status whose skill can actually act on it. Read those skills to see what each one consumes.
- **Make it a theme**: when several stuck bugs look like one root cause. Don't trust the bug reports — read the actual source they point at and confirm the cause is structural (in the code's design, fixable only by a refactor or an owner decision, not a one-off PR) before concluding evergreen can't fix it. Then record it once in the `themes` table — with this project's `project_id`, the `file:line` evidence, the change that would resolve it, and whether that change is worth it (`/worth-it`) — reusing a fitting existing theme **for this project** if there is one, and link the bugs via `theme_id` instead of leaving them to recur individually.

Leave issues that are legitimately mid-flight (open PR, recently worked) alone. Post a short `/discord-evergreen` heads-up when you open a new theme.
