Send a message via the evergreen Discord bot.

Use `/read-config-evergreen` to get the `discord_channel_id` and `owner_name`.

Use `python3 scripts/discord_send.py "message" --channel <discord_channel_id>`. By default it waits up to 3 hours for a reply (printed to stdout). Use `--no-wait` to skip waiting for a reply (still confirms delivery takes ~60s); use `--timeout SECONDS` for a custom wait.

If discord chat v2 is active (`configs` key `discord_chat` = `v2`), always send with `--no-wait` — replies are handled live by the chat agent, so a waiting poll would never see them.

Run the send command as a background task so you get notified when the owner replies rather than blocking.

The script auto-starts the bot if it's not running. Use `@<owner_name>` in the message to ping them.

To check for unsolicited messages (owner messaged the bot without a prior outbound), query:
```sql
SELECT * FROM discord_messages WHERE direction = 'inbound' AND read_at IS NULL ORDER BY created_at ASC
```
