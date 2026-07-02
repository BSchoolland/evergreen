# Run the Swarm

A swarm of browser agents drives a real browser through user journeys on the
project's **staging** site and reports behavioral bugs that log-watching can't see:
broken flows, auth/paywall bypasses, dead ends, lost progress. This is a second,
independent bug source alongside `/check-bugs-evergreen` — the browser agents
exercise the product, and the app's own telemetry judges each run.

Use `/read-config-evergreen` for project context and `EVERGREEN_PROJECT_ID`. The
project's `config` carries `swarm_adapter` — the browser-swarm adapter slug to run.
**If `swarm_adapter` is unset, this project has no swarm; stop here (nothing to do).**

## Run it

The swarm lives at `/home/ben/Projects/browser-swarm`. Run the full suite against
staging — it provisions throwaway accounts, drives every journey in parallel, reads
the oracle, and tears the accounts down. It takes ~15–20 min and spawns its own
browser agents; let it finish.

```sh
cd /home/ben/Projects/browser-swarm
export PATH="$HOME/.bun/bin:$HOME/.local/bin:$PATH"
python3 swarm.py run --adapter <swarm_adapter> --engine claude
```

## Read the results

Everything lands in `~/.browser-swarm/swarm.db`. Take the newest run:

```sh
DB=~/.browser-swarm/swarm.db
sqlite3 "$DB" "SELECT id, oracle_verdict, summary FROM swarm_runs ORDER BY id DESC LIMIT 1;"
RID=<that id>
sqlite3 "$DB" "SELECT source, severity, title, detail FROM findings WHERE run_id=$RID ORDER BY severity;"
sqlite3 "$DB" "SELECT journey, fixture, status, completed, substr(report_json,1,4000) FROM cells WHERE run_id=$RID;"
```

Two kinds of finding:
- **oracle** — server-side badness (5xx / error-level / impossible states) seen in
  the target's *own* telemetry during the run. Real defects.
- **agent** — what the browser agent observed. `high`/`medium` are the valuable ones
  (a paywall that only blocks in the UI, a professor reaching admin, a flow that
  dead-ends). `low` is usually environment noise — missing analytics keys, blocked
  third-party requests, RSC prefetch chatter — **do not file those.**

A cell with `status=error` or `completed=0` is worth a look, but tell a real app
failure apart from the agent running out of time or a flaky harness. The oracle
verdict and the app's telemetry outrank any single agent's claim.

## File findings

For each **genuine** bug (not noise, not agent flakiness), confirm it in the project
codebase, then record it against this project, mirroring `/check-bugs-evergreen`'s
discipline:

```sh
python3 scripts/record_bug.py add --project-id "$EVERGREEN_PROJECT_ID" \
  --environment staging --severity <critical|high|medium|low> \
  --error-pattern "<stable signature, e.g. route + behavior>" \
  --summary "<what's wrong and how the swarm observed it>" \
  --probable-root-cause "<if the swarm localized it>"
```

- **Dedupe first**: `record_bug.py list --open` and `list --dismissed` — bump
  occurrence counts / skip dismissed patterns instead of re-filing.
- Note any recent commit or open PR that may already address it.

It is completely fine to find nothing — record only demonstrable bugs.
