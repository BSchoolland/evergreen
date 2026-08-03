# Evergreen Discord bot v2 — a dumb router

Maps Discord threads to detached `claude -p` sessions. **All conversational
behavior lives in `.claude/skills/discord-live/SKILL.md`** — edit that, not
this code. The bot never reads message content to make decisions; its state
is integers and UUIDs only.

## The state machine (per thread)

| Event | Action |
|---|---|
| Owner message in main channel | create thread, mint session UUID, spawn agent |
| Message in a thread | debounce 2s, SIGTERM live agent if any, `claude -p --resume <uuid>` with the message appended |
| Agent exits | **nothing** (no crash detection — the human's next message is the retry) |
| Bot boot | sweep known threads for messages newer than `lastDeliveredId`, deliver them |

Key facts (all verified by test):

- **SIGTERM is claude's native interrupt**: reaps its tool children, writes
  `[Request interrupted]` to the transcript, exits in ~2-4s. Fallback for a
  stuck agent is `pkill -9 -s <pid>` (session kill — tool children have their
  own process groups, plain kill would orphan them).
- **Sessions are cwd-bound.** Agents always spawn with cwd = repo root. Moving
  the repo orphans existing conversations (new messages there will start fresh
  context; nothing crashes).
- A message reaches an agent in exactly one way: inside its spawn prompt.
  There is no second delivery path, hence no delivery races.
- Agents are **detached** (`KillMode=process` in the unit): restarting the bot
  never kills running agents. A restarted bot re-adopts them via pid +
  `/proc/<pid>/cmdline`-contains-session-UUID.

## Relay (watchdog notifications)

Drains `outbound` rows from `$EVERGREEN_HOME/evergreen.db` queued by
`scripts/discord_send.py`. Claim-before-send: any failure marks the row
`send_error` and it is never re-picked — re-send loops are structurally
impossible. Backlog older than 10 min at boot is skipped, not dumped.
Replies to relay messages are recorded as `inbound` rows so
`discord_send.py --wait-reply` works. Chat threads never touch the DB.

## Ops

```sh
systemctl --user {start,stop,restart,status} evergreen-team-discord
journalctl --user -u evergreen-team-discord -f     # bot logs
state/logs/<threadId>.log                          # per-agent stdout/stderr
state/threads.json                                 # threadId -> {sessionId, pid, lastDeliveredId}
```

Config in `.env`: `DISCORD_TOKEN`, `DISCORD_CHANNEL_ID`, `DISCORD_OWNER_ID`
(comma-separated), optional `DISCORD_AGENT_MODEL`, `DISCORD_PERMISSION_MODE`
(default `bypassPermissions`), `CLAUDE_BIN`.

To reset a conversation: delete its entry from `state/threads.json` (next
message starts a fresh session). To reset everything: stop bot, delete
`state/threads.json`, start bot.
