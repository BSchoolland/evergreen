# Run the Swarm

A swarm of browser agents drives a real browser through a project's **staging** site
and reports behavioral bugs that log-watching misses — broken flows, auth/paywall
bypasses, dead ends. A second bug source alongside `/check-bugs-evergreen`.

`/read-config-evergreen` gives you `EVERGREEN_PROJECT_ID` and the project's
`swarm_adapter`. If `swarm_adapter` is unset, this project has no swarm — stop.

Run the full suite (it provisions throwaway accounts, drives every journey, and tears
them down — ~15–20 min):

```sh
cd /home/ben/Projects/browser-swarm
export PATH="$HOME/.bun/bin:$HOME/.local/bin:$PATH"
python3 swarm.py run --adapter <swarm_adapter> --engine claude
```

This runs longer than a single command allows, so start it in the background and poll
`swarm_runs` until this run's `finished_at` is set — keep waiting; do not read results
or end your turn while it's still running.

Read the latest run from `~/.browser-swarm/swarm.db` (tables `swarm_runs`, `findings`,
`cells`). Findings have two sources: the **oracle** (the app's own telemetry — real
server-side defects) and the **agent** (what the browser observed — trust the telemetry
over any single agent's claim, and expect it to over-report low-severity noise).

File genuine bugs against this project with `record_bug.py add --project-id
"$EVERGREEN_PROJECT_ID" --environment staging`, with the same confirm-in-code and dedupe
discipline as `/check-bugs-evergreen`. Finding nothing is fine.
