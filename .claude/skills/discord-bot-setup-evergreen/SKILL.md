Set up the evergreen Discord bot at `services/discord-bot/`.

1. Walk the user through creating a Discord bot at https://discord.com/developers/applications — they need to create an app, add a bot, copy the token, enable Message Content Intent, and invite it to their server with Send Messages + Read Message History permissions. Ask them for the bot token and channel ID.
2. Create `services/discord-bot/.env` from `.env.example` with their values.
3. Run `npm install` in `services/discord-bot/`.
4. Start the bot with `node services/discord-bot/index.js` and verify it logs in.
5. Send a test message and await a reply using `python3 scripts/discord_send.py "Reply to this message to complete setup." --channel $CHANNEL_ID --wait-reply --timeout 120`.
6. After the reply comes in, read the `author_id` from the inbound reply row in the `discord_messages` table (`SELECT author_id FROM discord_messages WHERE direction = 'inbound' ORDER BY created_at DESC LIMIT 1`). Add `DISCORD_OWNER_ID=<that id>` to `services/discord-bot/.env`. This is the user the bot will @mention for notifications.
7. Restart the bot and send a final test: `python3 scripts/discord_send.py "@Ben Setup complete!" --channel $CHANNEL_ID` to confirm mentions work.
