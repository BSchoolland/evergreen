# Evergreen Setup

First-time setup for evergreen. Run each section in order.

## 1. Project config

Ask the user the following questions and store each answer in the `configs` table in `~/.evergreen/evergreen.db`:

```sql
INSERT OR REPLACE INTO configs (key, value) VALUES (?, ?);
```

Questions:
- **project_name**: "What's the name of the project you want to monitor?"
- **project_path**: "What's the absolute path to the project repo?" (verify it exists)
- **ssh_staging**: "What's the SSH alias for your staging server?" (check `~/.ssh/config` and suggest matches)
- **ssh_prod**: "What's the SSH alias for your production server?" (check `~/.ssh/config` and suggest matches)
- **owner_name**: "What's your first name? (used for Discord notifications)"

## 2. Discord bot

Run `/discord-bot-setup-evergreen` to walk the user through creating and connecting the Discord bot. This is interactive — the user needs to create a bot on Discord's developer portal and provide credentials.

After the bot is set up, store the channel ID:
```sql
INSERT OR REPLACE INTO configs (key, value) VALUES ('discord_channel_id', ?);
```

Start the bot in the background:
```bash
nohup node services/discord-bot/index.js >> ~/.evergreen/discord-bot.log 2>&1 &
echo $! > ~/.evergreen/discord-bot.pid
```

## 3. Cron server

Run `/setup-cron-evergreen` to let the user configure skill intervals (or accept the defaults: check-bugs every 1h, hackernews-monitor every 6h, dependency-audit every 24h, triage runs automatically after any check if new items exist).

Then start the server:
```bash
bash scripts/server.sh start
```

## 4. Verify

Confirm everything is running:
```bash
bash scripts/server.sh status
```

Check the Discord bot is alive by sending a test message (get the channel ID from configs):
```bash
python3 scripts/discord_send.py "Evergreen is online." --channel <discord_channel_id>
```

Tell the user setup is complete and summarize what's running:
- Discord bot (pid in `~/.evergreen/discord-bot.pid`, logs in `~/.evergreen/discord-bot.log`)
- Cron server (pid in `~/.evergreen/server.pid`, logs in `~/.evergreen/server.log`)
- How to check status: `bash scripts/server.sh status`
- How to open an interactive session: `python evergreen.py`
