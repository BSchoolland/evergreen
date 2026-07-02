# Run the Swarm

A swarm of browser agents drives a real browser through a project's **staging** site
and reports behavioral bugs log-watching misses — broken flows, auth/paywall bypasses,
dead ends. A second bug source alongside `/check-bugs-evergreen`.

`/read-config-evergreen` gives `EVERGREEN_PROJECT_ID` and the project's `swarm_adapter`.
If `swarm_adapter` is unset, this project has no swarm — stop.

A run takes ~15–20 min — too long to wait on in one session. So each pass **files the
last completed run's findings, then launches a fresh run detached** for the next pass to
file. A day's lag is fine.

**File findings.** From `~/.browser-swarm/swarm.db` (tables `swarm_runs`, `findings`,
`cells`), take this adapter's newest run with a non-null `finished_at`. Findings have two
sources — the **oracle** (the app's own telemetry: real server-side defects) and the
**agent** (what the browser saw: trust the telemetry over any single agent's claim, and
expect low-severity noise). File genuine ones with `record_bug.py add --project-id
"$EVERGREEN_PROJECT_ID" --environment staging`, same confirm-in-code and dedupe discipline
as `/check-bugs-evergreen`. Dedupe means re-reading a run you already filed finds nothing
new — that's fine.

**Launch the next run, detached** so it outlives this session:

```sh
cd /home/ben/Projects/browser-swarm
export PATH="$HOME/.bun/bin:$HOME/.local/bin:$PATH"
setsid nohup python3 swarm.py run --adapter <swarm_adapter> --engine claude \
  >/tmp/swarm-<swarm_adapter>.log 2>&1 &
```

Confirm a new `swarm_runs` row appeared, then stop — don't wait for it.
