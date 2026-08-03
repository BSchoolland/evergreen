Search the full Discord history — fetched live from the Discord API (every channel
and thread the bot can see, back to the server's first message), synced into a local
archive and searched full-text. This is the real history; the bot's `discord_messages`
table only holds what the bot happened to witness, so never use it for research.

```
python3 scripts/discord_search.py "docker cve"                # full-text, results printed oldest-first
python3 scripts/discord_search.py --author kirill --days 14   # filters work with or without a query
python3 scripts/discord_search.py --around 27                 # the conversation surrounding row id 27
python3 scripts/discord_search.py "pr" --channel team --full  # untruncated content
```

Each result starts with its row id — feed that to `--around` to read the surrounding
discussion. The tool auto-syncs new messages when its archive is stale (>10 min);
`--sync` forces a refresh, `--no-sync` skips it for speed.
