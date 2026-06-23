# Onboard a Project

Add a new project to this evergreen instance and bring it under monitoring. Driven
by `scripts/project.py`. Be conversational and do the discovery yourself — the owner
should answer as little as possible.

## 0. Pick the type

Every project is one of:
- **static** — a site you don't run code/DB for (portfolio, brochure, client site).
  Monitoring plane only: uptime + SSL. No agent skills, ~free.
- **wordpress** — a WordPress site, alert-only today. Uptime + SSL now; wp-cli/wpscan
  checks are a planned add. Needs SSH to the host if you want deeper checks later.
- **code_db** — an app you own with a repo + database (TACOS-style). Full agent plane:
  log bug-hunting, dependency audit, PR fixes. Needs repo path + SSH to staging/prod.

If unsure from the URL, ask one question to classify.

## 1. Gather what the type needs

Always: `--name`, `--base-url`, `--tier` (lite | standard | deep; default standard —
deeper = more frequent agent runs = more spend; static/wordpress ignore tier for now).

- **static**: nothing else.
- **wordpress**: `--ssh <alias>` and `--wp-path <dir>` if we have server access (check
  `~/.ssh/config` and suggest matches). Optional today.
- **code_db**: `--project-path <repo>` (verify it exists / offer to clone), `--ssh-staging`
  and `--ssh-prod` (suggest matches from `~/.ssh/config`).

Optional for any: `--alert-channel <discord_channel_id>` to route this project's alerts
to its own channel (defaults to the instance channel), and `--failure-threshold <n>`
(consecutive failed probes before a DOWN alert; default 3).

**Auto-discover before asking.** Grep `~/.ssh/config` for hosts matching the domain.
If the base_url's host is one you can SSH to, offer to detect the stack. For code_db,
peek at the repo to confirm it's the right one.

## 2. Create it

```
python3 scripts/project.py add --name "<name>" --type <type> --base-url <url> --tier <tier> [type-specific flags]
```

This creates the project row, seeds its agent-plane cron schedule from (type, tier),
runs a **baseline uptime check**, and prints the result. Confirm the baseline check
shows the site reachable — if not, investigate (wrong URL? auth wall? firewall?) before
moving on.

## 3. Verify access (code_db / wordpress with SSH)

Confirm the credentials actually work:
- code_db: `ssh <ssh_staging> 'echo ok'`, and that you can reach the project DB (use
  `/check-bugs-evergreen`'s discovery, but don't file anything — just confirm access).
- wordpress: `ssh <ssh> 'wp --path=<wp_path> core version'` if wp-cli is present.

## 4. Confirm and report

Show the owner the final state:
```
python3 scripts/project.py show <slug>
```
Summarize: type, tier, what's monitored (uptime always; agent skills only for code_db),
alert channel, and the baseline uptime result. The watchdog picks the project up on its
next tick — uptime within ~a minute, agent skills on their schedule. No restart needed.

## Notes
- Re-running is safe: slugs auto-disambiguate; cron seeding is INSERT-OR-IGNORE.
- Change depth later: `project.py set-tier <slug> <tier> --reset`.
- Pause/resume monitoring: `project.py pause|resume <slug>`.
