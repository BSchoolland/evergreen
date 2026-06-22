# Read Evergreen Config

Query the `configs` table in `~/.evergreen/evergreen.db` to get environment-specific values.

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
echo "${EVERGREEN_PROJECT_PATH:-$(sqlite3 ~/.evergreen/evergreen.db "SELECT value FROM configs WHERE key='project_path'")}"
```
