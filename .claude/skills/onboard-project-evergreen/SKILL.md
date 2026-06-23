# Onboard a Project

Interactive, and the owner's first encounter with evergreen — so talk in plain terms,
never the internal vocabulary (no "agent plane", "data note", or type names).

Ask what they want kept an eye on and why — their goal, not a category. Collect whatever
access reaching it needs (ssh alias, database credentials, secrets); ask for anything you
can't find yourself.

Explore and work it out: probe the URL, match a host in `~/.ssh/config`, get in if you
can, and find where the project's real health signal lives (a database, logs via
pm2/journald/docker, a repo).

Before setting anything up, make the owner understand what evergreen will actually do for
this project. Learn it from the source rather than guessing — read its skills
(`.claude/skills/`) and how they're scheduled (`evergreen.tiers` and the cron server) — then
explain it plainly and in depth, including how often each thing runs and a rough monthly cost
(use existing runs as a guide). Let them make it lighter or heavier; they should leave knowing
what it will do and agreeing to it.

Then do it with `scripts/project.py` (run it to see its commands): choose the type and tier
matching the agreed depth, write a short data note — where the signal lives plus the exact
commands to read it, real names filled in — and store the access and repo it needs. Confirm
the baseline uptime check came back reachable.

Finally, prove it works. Manually trigger the bug-detection skill for this project
(`scripts/trigger-skill.py check-bugs --project <slug>`), wait for the watchdog to run it,
then read that run's history and outcome — confirm it reached the data source and finished
cleanly (found real issues or correctly found nothing), not an error. If it misread the
source or couldn't connect, fix the data note or access and trigger again. (A monitoring-only
project has no detector — its baseline uptime check is the proof.) Recap in plain words.

Re-run onboard anytime to re-explore and refresh a project's data note.
