# Evergreen Discord bot — notifications + interactive dev

This one process is evergreen's Discord front-end. It plays two roles:

1. **Notification relay (unchanged).** The watchdog queues messages in the
   `discord_messages` table; the bot posts them to the channel and records replies.
   See the outbound/reply pollers in `index.js`.

2. **Interactive dev (new).** You talk to evergreen in a **thread** and it does work
   live — on Pi, with evergreen's skills and DB, in its own clone of the target repo.

## The two-dev model

Evergreen runs as two "devs" that share this machine + the `~/.evergreen/evergreen.db`
state, but **never share a git working tree**:

| Dev | Engine | Clone | Driven by |
|-----|--------|-------|-----------|
| **Watchdog** | `pi -p` (scheduled) | `project_path` | `scripts/evergreen-server.py` |
| **Interactive** | `pi --mode rpc` (persistent) | `project_path_interactive` | this bot |

Each actor sets `EVERGREEN_PROJECT_PATH` to its own clone; skills resolve the project
path from that env var (see `db.get_project_path`). Separate clones = no collisions,
no locks, no worktree lifecycle to manage.

## How a conversation works

- **Start one:** @mention the bot in the channel. It opens a thread and seeds the
  conversation with your message.
- **Continue:** every message you send in that thread is a turn. If the agent is
  mid-task, your message is delivered as a **steer** (it adjusts course before the
  next model call) rather than queued behind the whole run.
- **Memory:** each thread maps to one persistent Pi session file
  (`.pi/sessions/threads/<threadId>/`), tracked in the `conversations` table. The
  child process is evicted after 30 min idle and **resumed from the session file**
  on your next message — context survives.
- **Approvals:** the agent works freely in its sandbox clone (read/edit/write/ordinary
  bash), but outward or irreversible commands (`git push`, `gh pr create`, `rm -rf`,
  `deploy`, …) pause for an **Allow/Deny button**. Implemented by the Pi extension
  `.pi/extensions/evergreen-approval.js`, which is env-gated to the interactive host
  so the watchdog still runs autonomously. Only the owner (`DISCORD_OWNER_ID`) can
  drive the agent or click approve.

## Files

- `index.js` — Discord client; routes thread messages → interactive, channel
  @mentions → new conversation, everything else → the notification relay.
- `piThread.js` — one `pi --mode rpc` child per thread (JSONL framing, prompt/steer,
  idle eviction, resume). Emits high-level events.
- `interactive.js` — wires threads ↔ `PiThread`: throttled streaming edits, a working
  status line, approval/select buttons, free-text input capture.
- `db.js` — `discord_messages` + `conversations` + config helpers.

## Running

```sh
# one-time: create the interactive dev's clone and record its path
bash ../../scripts/setup-interactive-clone.sh
cd <project_path_interactive> && npm install   # so the dev can build/test there

# start the bot (auto-syncs .pi/skills on boot)
node index.js
```

Config it reads from the `configs` table: `project_path_interactive` (falls back to
`project_path` with a collision warning), `pi_model` (optional `provider/model`),
plus `DISCORD_TOKEN` / `DISCORD_CHANNEL_ID` / `DISCORD_OWNER_ID` from `.env`.
