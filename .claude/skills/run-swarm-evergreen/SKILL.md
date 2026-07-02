# Run the Swarm

A swarm of browser agents drives a real browser through a project's **staging** site
and reports behavioral bugs log-watching misses — broken flows, auth/paywall bypasses,
dead ends. A second bug source alongside `/check-bugs-evergreen`.

`/read-config-evergreen` gives `EVERGREEN_PROJECT_ID` and the project's `swarm_adapter`.
If `swarm_adapter` is unset, this project has no swarm — stop.

Run the full suite as a **single foreground command** and wait for it. It takes ~8 min,
so give the command the full 10-minute timeout. Do NOT run it in the background — a
backgrounded run is killed when this session ends, so you'd never get results:

```sh
cd /home/ben/Projects/browser-swarm
export PATH="$HOME/.bun/bin:$HOME/.local/bin:$PATH"
python3 swarm.py run --adapter <swarm_adapter> --engine claude --timeout 540
```

When it returns, read that run from `~/.browser-swarm/swarm.db` (tables `swarm_runs`,
`findings`, `cells`). Findings have two sources — the **oracle** (the app's own
telemetry: real server-side defects) and the **agent** (what the browser saw: trust the
telemetry over any single agent's claim, and expect low-severity noise).

File genuine bugs with `record_bug.py add --project-id "$EVERGREEN_PROJECT_ID"
--environment staging`, same confirm-in-code and dedupe discipline as
`/check-bugs-evergreen`. Finding nothing is fine.
