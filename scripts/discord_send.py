#!/usr/bin/env python3
"""Send a Discord message via the evergreen bot. Queues a message in the shared DB;
the bot process picks it up and sends it. With --wait-reply, polls for a reply."""

import argparse
import os
import subprocess
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from evergreen.db import epoch

DB_PATH = Path.home() / ".evergreen" / "evergreen.db"
BOT_DIR = Path(__file__).resolve().parent.parent / "services" / "discord-bot"
BOT_PID_FILE = Path.home() / ".evergreen" / "discord-bot.pid"

def get_conn():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS discord_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            discord_message_id TEXT UNIQUE,
            channel_id TEXT NOT NULL,
            direction TEXT NOT NULL CHECK(direction IN ('outbound', 'inbound')),
            author_id TEXT,
            content TEXT NOT NULL,
            reply_to_message_id TEXT,
            created_at INTEGER NOT NULL DEFAULT (cast(strftime('%s', 'now') as integer)),
            read_at INTEGER
        )
    """)
    return conn

def _bot_running():
    """True if the Discord bot is already up. The bot is launched as
    `node index.js` with cwd=BOT_DIR (by server.sh and below), so its command
    line is literally "node index.js" with no path — match that, and prefer the
    PID file server.sh maintains. (The old pattern looked for the full path, which
    never matched, so every send spawned a duplicate bot → duplicate messages.)"""
    try:
        pid = int(BOT_PID_FILE.read_text().strip())
        os.kill(pid, 0)
        return True
    except (FileNotFoundError, ValueError, ProcessLookupError, PermissionError):
        pass
    try:
        return subprocess.run(["pgrep", "-f", "node index.js"],
                              capture_output=True).returncode == 0
    except FileNotFoundError:
        return False


def ensure_bot_running():
    """Start the Discord bot only if it's genuinely not running."""
    if _bot_running():
        return
    print("Bot not running, starting it...", file=sys.stderr)
    proc = subprocess.Popen(
        ["node", "index.js"],
        cwd=str(BOT_DIR),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    # Record the pid so the next send sees this instance and won't spawn another.
    try:
        BOT_PID_FILE.write_text(f"{proc.pid}\n")
    except OSError:
        pass
    time.sleep(5)


def send(channel_id, content):
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO discord_messages (channel_id, direction, content) VALUES (?, 'outbound', ?)",
        (channel_id, content),
    )
    conn.commit()
    row_id = cur.lastrowid
    conn.close()
    print(f"Queued message #{row_id}", file=sys.stderr)
    return row_id

def wait_for_sent(row_id, timeout=60):
    """Wait until the bot has actually sent the message and recorded the discord_message_id."""
    conn = get_conn()
    deadline = time.time() + timeout
    while time.time() < deadline:
        row = conn.execute(
            "SELECT discord_message_id FROM discord_messages WHERE id = ?", (row_id,)
        ).fetchone()
        if row and row["discord_message_id"]:
            conn.close()
            return row["discord_message_id"]
        time.sleep(2)
    conn.close()
    print("Timed out waiting for bot to send message", file=sys.stderr)
    sys.exit(1)

def wait_for_reply(discord_message_id, timeout=1800):
    """Poll for an inbound reply to the given discord message. Returns reply content."""
    conn = get_conn()
    deadline = time.time() + timeout
    print(f"Waiting up to {timeout}s for reply...", file=sys.stderr)
    while time.time() < deadline:
        rows = conn.execute(
            "SELECT * FROM discord_messages WHERE direction = 'inbound' AND reply_to_message_id = ? AND read_at IS NULL ORDER BY created_at ASC",
            (discord_message_id,),
        ).fetchall()
        if rows:
            for row in rows:
                conn.execute("UPDATE discord_messages SET read_at = ? WHERE id = ?", (epoch(), row["id"]))
            conn.commit()
            conn.close()
            reply = "\n".join(r["content"] for r in rows)
            print(reply)
            return reply
        time.sleep(5)
    conn.close()
    print("No reply received within timeout", file=sys.stderr)
    return None

def main():
    parser = argparse.ArgumentParser(description="Send a message via the evergreen Discord bot")
    parser.add_argument("message", help="Message content to send")
    parser.add_argument("--channel", required=True, help="Discord channel ID")
    parser.add_argument("--no-wait", action="store_true", help="Don't wait for a reply (fire and forget)")
    parser.add_argument("--timeout", type=int, default=10800, help="Reply timeout in seconds (default: 3 hours)")

    args = parser.parse_args()

    ensure_bot_running()
    row_id = send(args.channel, args.message)

    if not args.no_wait:
        discord_msg_id = wait_for_sent(row_id)
        reply = wait_for_reply(discord_msg_id, timeout=args.timeout)
        sys.exit(0 if reply else 1)

if __name__ == "__main__":
    main()
