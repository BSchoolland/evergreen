# Onboard a Project

Interactive. Add a project to this evergreen instance and work out, with the owner,
where its monitoring signal actually lives. Launched by `evergreen.py onboard [hint]`.

Discover the project's data — don't assume it. Get the name and URL from the owner (a
hint may be passed in). Pick a type — it just presets which agent skills run
(`evergreen.tiers`): `static`/`wordpress` = monitoring plane only; `code_db`/`server_code`
= full agent plane (the difference between those two is only what the data note says).

Then explore. Use the ssh aliases in `~/.ssh/config` (suggest matches for the domain),
SSH in, and find what's really there: a database? pm2? docker? journald/systemd? an app
log directory? a repo? Show the owner what you found and confirm it before relying on it.

Write a one-paragraph **data note**: where the signal lives and the exact commands to
reach it — e.g. "No DB; signal is `pm2 logs benjamin-schoolland` on ssh personal-server"
or "Postgres via SSM /x/DATABASE_URL on ssh staging/prod; find schema with `\d`". This is
what the detection skills read, so keep it concrete and short.

Create it:
```
python3 scripts/project.py add --name "<name>" --type <type> --base-url <url> [--ssh <alias>] [--tier lite|standard|deep]
python3 scripts/project.py set-config <id> data_note "<the note>"
```
Set `repo`, `wp_path`, `ssh_staging`/`ssh_prod` as the type needs. `add` runs a baseline
uptime check — confirm the site came back reachable.

Verify the access you'll depend on actually works (ssh connects; the DB/logs are
readable). If the project should get PR fixes, make sure a `repo` is configured.

Re-running onboard on an existing project is how its data note stays current — re-explore
and update it when the project changes (new logging, a migration, dockerized).

Finish with `python3 scripts/project.py show <slug>` and a short summary. The watchdog
picks the project up on its next tick — no restart needed.
