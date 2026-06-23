# Onboard a Project

Interactive, and the owner's first encounter with evergreen — so talk in plain terms,
never the internal vocabulary (no "agent plane", "data note", or type names).

Ask what they want kept an eye on and why — their goal, not a category. Collect whatever
access reaching it needs (ssh alias, database credentials, secrets); ask for anything you
can't find yourself.

Explore and work it out: probe the URL, match a host in `~/.ssh/config`, get in if you
can, and find where the project's real health signal lives (a database, logs via
pm2/journald/docker, a repo).

Before setting anything up, tell them plainly everything evergreen will do here and how
often — check uptime, read real data and logs from their system, scan security news daily
for threats to their stack, open fixes for what it finds — and tune the depth to what they
actually want. They should leave knowing exactly what it will do.

Then do it with `scripts/project.py` (run it to see its commands): choose the type that
matches the agreed depth, write a short data note — where the signal lives plus the exact
commands to read it, real names filled in — and store the access and repo it needs.
Confirm the baseline uptime check came back reachable, and recap in plain words.

Re-run onboard anytime to re-explore and refresh a project's data note.
