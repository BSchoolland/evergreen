Send a message to Ben via the evergreen Discord bot.

Use `python3 scripts/discord_send.py "message" --channel $CHANNEL_ID`. By default it waits up to 3 hours for a reply (printed to stdout). Use `--no-wait` for fire-and-forget, or `--timeout SECONDS` for a custom wait.

Run the send command as a background task so you get notified when Ben replies rather than blocking.

The script auto-starts the bot if it's not running. Use `@Ben` in the message to ping him.

To check for unsolicited messages (Ben messaged the bot without a prior outbound), query:
```sql
SELECT * FROM discord_messages WHERE direction = 'inbound' AND read_at IS NULL ORDER BY created_at ASC
```
