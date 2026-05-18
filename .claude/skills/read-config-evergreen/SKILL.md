# Read Evergreen Config

Query the `configs` table in `~/.evergreen/evergreen.db` to get environment-specific values.

```sql
SELECT key, value FROM configs ORDER BY key;
```

Available config keys:
- `project_name` — Name of the monitored project
- `project_path` — Absolute path to the project repo
- `ssh_staging` — SSH alias for the staging server
- `ssh_prod` — SSH alias for the production server
- `discord_channel_id` — Discord channel ID for notifications
- `owner_name` — Name of the person to notify
