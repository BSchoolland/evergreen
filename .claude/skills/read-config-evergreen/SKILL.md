# Read Evergreen Config

Resolve environment-specific values from the evergreen DB. The DB lives at
`${EVERGREEN_HOME:-$HOME/.evergreen}/evergreen.db` — never hardcode `~/.evergreen`.

```sh
DB="${EVERGREEN_HOME:-$HOME/.evergreen}/evergreen.db"
```

There are two modes, selected by whether `EVERGREEN_PROJECT_ID` is set in your
environment.

## Multi-project mode (`EVERGREEN_PROJECT_ID` is set)

Return *that project's* config. Read its row from the `projects` table and expose
its columns plus the keys inside its `config` JSON blob, merged over the legacy
global `configs` table (project values win):

```sh
PID="$EVERGREEN_PROJECT_ID"

# Project row: name, type, base_url, depth_tier.
sqlite3 "$DB" "SELECT name, type, base_url, depth_tier FROM projects WHERE id=$PID;"

# Per-project config JSON, flattened to key=value lines (project_path, ssh_staging,
# ssh_prod, project_path_interactive, alert_channel_id, wp_ssh, wp_path, ...):
sqlite3 "$DB" "SELECT key || '=' || value FROM json_each((SELECT config FROM projects WHERE id=$PID));"

# Legacy global fallback (only for keys the project row/JSON don't define):
sqlite3 "$DB" "SELECT key || '=' || value FROM configs ORDER BY key;"
```

The effective config is the global `configs` rows with the project row + its
`config` JSON merged *over* them. Map the project columns to the familiar keys:

- `project_name` ← `projects.name`
- `project_type` ← `projects.type` (`code_db` | `wordpress` | `static`)
- `base_url` ← `projects.base_url`
- `depth_tier` ← `projects.depth_tier`
- `project_path`, `project_path_interactive`, `ssh_staging`, `ssh_prod`, `ssh`,
  `repo`, `alert_channel_id`, `discord_channel_id`, etc. ← keys inside
  `projects.config`, falling back to the global `configs` value when omitted.
- `data_note` ← a short paragraph (in `projects.config`) describing **where this
  project's monitoring signal lives and how to reach it** — a database, app/server
  logs (pm2 / journalctl / docker), etc. Detection skills follow it instead of
  assuming a database. Written/updated by onboarding (`evergreen.py onboard`).

One-liner to read a single project config key with global fallback:

```sh
sqlite3 "$DB" "SELECT coalesce(json_extract((SELECT config FROM projects WHERE id=$PID), '\$.project_path'), (SELECT value FROM configs WHERE key='project_path'));"
```

## Single-project mode (`EVERGREEN_PROJECT_ID` is unset)

Behave exactly as before: read everything from the global `configs` table. This
keeps existing TACOS (project 1) callers working unchanged.

```sql
SELECT key, value FROM configs ORDER BY key;
```

Available config keys:
- `project_name` — Name of the monitored project
- `project_path` — Absolute path to the **watchdog's** clone of the project repo
- `project_path_interactive` — Absolute path to the **interactive dev's** clone of the project repo (a separate clone so the two never collide on git state)
- `ssh_staging` — SSH alias for the staging server
- `ssh_prod` — SSH alias for the production server
- `discord_channel_id` — Discord channel ID for notifications
- `discord_model` — Optional `provider/model` for Discord interactive Pi sessions (falls back to `pi_model`, then pi default)
- `discord_thinking` — Optional Pi thinking level for Discord interactive Pi sessions
- `owner_name` — Name of the person to notify
- `pi_model` — Optional `provider/model` for pi (e.g. `anthropic/claude-opus-4-5`); unset = pi default

**Important:** always use the project path for *your* clone. Each actor (watchdog vs
interactive dev) sets `EVERGREEN_PROJECT_PATH` in its environment to its own clone, and
that env var overrides `project_path`. So prefer `$EVERGREEN_PROJECT_PATH` when set:

```sh
echo "${EVERGREEN_PROJECT_PATH:-$(sqlite3 "$DB" "SELECT value FROM configs WHERE key='project_path'")}"
```

## Project scoping for other skills

When `EVERGREEN_PROJECT_ID` is set, it is the scope for everything you do this run:
pass it as `--project-id "$EVERGREEN_PROJECT_ID"` to `record_bug.py` / `record_alert.py`,
and filter `bugs` / `security_alerts` queries by `project_id = $EVERGREEN_PROJECT_ID`.
When it's unset, omit the flag and don't filter — the scripts default to project 1.
