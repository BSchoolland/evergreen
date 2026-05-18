# Evergreen Setup

First-time setup for evergreen. Run each section in order.

## 1. Discord bot

Run `/discord-bot-setup-evergreen` to walk the user through creating and connecting the Discord bot. This is interactive — the user needs to create a bot on Discord's developer portal and provide credentials.

Once complete, start the bot in the background:
```bash
nohup node services/discord-bot/index.js >> ~/.evergreen/discord-bot.log 2>&1 &
echo $! > ~/.evergreen/discord-bot.pid
```

## 2. Cron server

Run `/setup-cron-evergreen` to let the user configure skill intervals (or accept the defaults: check-bugs every 1h, hackernews-monitor every 6h, tacos-audit every 24h, triage runs automatically after any check if new items exist).

Then start the server:
```bash
bash scripts/server.sh start
```

## 3. Verify

Confirm everything is running:
```bash
bash scripts/server.sh status
```

Check the Discord bot is alive by sending a test message:
```bash
python3 scripts/discord_send.py "Evergreen is online." --channel $DISCORD_CHANNEL_ID
```

Tell the user setup is complete and summarize what's running:
- Discord bot (pid in `~/.evergreen/discord-bot.pid`, logs in `~/.evergreen/discord-bot.log`)
- Cron server (pid in `~/.evergreen/server.pid`, logs in `~/.evergreen/server.log`)
- How to check status: `bash scripts/server.sh status`
- How to open an interactive session: `python evergreen.py`
