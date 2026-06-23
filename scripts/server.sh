#!/usr/bin/env bash
# Usage: server.sh {start|stop|status|trigger <skill> [--no-force]}
#
# Manages evergreen's two long-lived processes:
#   - watchdog   : scheduled skill runner (evergreen-server.py)
#   - discordbot : Discord front-end / interactive dev (services/discord-bot/index.js)
set -euo pipefail

EVERGREEN_DIR="${EVERGREEN_HOME:-$HOME/.evergreen}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# --- watchdog ---
PID_FILE="$EVERGREEN_DIR/server.pid"
LOG_FILE="$EVERGREEN_DIR/server.log"
SERVER_SCRIPT="$SCRIPT_DIR/evergreen-server.py"

# --- discord bot ---
BOT_DIR="$SCRIPT_DIR/../services/discord-bot"
BOT_PID_FILE="$EVERGREEN_DIR/discord-bot.pid"
BOT_LOG_FILE="$EVERGREEN_DIR/discord-bot.log"

mkdir -p "$EVERGREEN_DIR"

is_running() { # $1 = pid file
  local f="$1"
  [ -f "$f" ] && kill -0 "$(cat "$f")" 2>/dev/null
}

start_watchdog() {
  if is_running "$PID_FILE"; then
    echo "Watchdog already running (pid $(cat "$PID_FILE"))"
    return
  fi
  nohup python3 "$SERVER_SCRIPT" >> "$LOG_FILE" 2>&1 &
  echo $! > "$PID_FILE"
  echo "Watchdog started (pid $!), logging to $LOG_FILE"
}

start_bot() {
  if is_running "$BOT_PID_FILE"; then
    echo "Discord bot already running (pid $(cat "$BOT_PID_FILE"))"
    return
  fi
  # index.js loads its .env from cwd, so start from the bot directory.
  # `exec` replaces the subshell with node so the PID we record is node's own.
  ( cd "$BOT_DIR" && exec nohup node index.js >> "$BOT_LOG_FILE" 2>&1 ) &
  echo $! > "$BOT_PID_FILE"
  echo "Discord bot started (pid $(cat "$BOT_PID_FILE")), logging to $BOT_LOG_FILE"
}

stop_one() { # $1 = pid file, $2 = label
  local f="$1" label="$2"
  if [ ! -f "$f" ]; then
    echo "$label: no PID file"
    return
  fi
  local pid; pid=$(cat "$f")
  if kill -0 "$pid" 2>/dev/null; then
    kill "$pid"
    echo "$label stopped (pid $pid)"
  else
    echo "$label: process $pid not running"
  fi
  rm -f "$f"
}

case "${1:-status}" in
  start)
    start_watchdog
    start_bot
    ;;
  stop)
    stop_one "$BOT_PID_FILE" "Discord bot"
    stop_one "$PID_FILE" "Watchdog"
    ;;
  trigger)
    shift
    python3 "$SCRIPT_DIR/trigger-skill.py" "$@"
    ;;
  status)
    if is_running "$BOT_PID_FILE"; then
      echo "Discord bot: running (pid $(cat "$BOT_PID_FILE")), log: $BOT_LOG_FILE"
    else
      echo "Discord bot: not running"
    fi
    echo ""
    if is_running "$PID_FILE"; then
      echo "Watchdog: running (pid $(cat "$PID_FILE")), log: $LOG_FILE"
      echo ""
      echo "Schedule:"
      python3 -c "
import sys; sys.path.insert(0, '$SCRIPT_DIR/../src')
from evergreen.db import get_connection
conn = get_connection()
for skill, interval, enabled, last in conn.execute('SELECT skill, interval_minutes, enabled, last_run_at FROM cron_jobs ORDER BY skill'):
    status = 'enabled' if enabled else 'disabled'
    last_str = last or 'never'
    print(f'  {skill}: every {interval}m ({status}), last run: {last_str}')
conn.close()
"
    else
      echo "Watchdog: not running"
    fi
    ;;
  *)
    echo "Usage: $0 {start|stop|status|trigger <skill> [--no-force]}"
    exit 1
    ;;
esac
