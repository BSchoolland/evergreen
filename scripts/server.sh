#!/usr/bin/env bash
# Usage: server.sh {start|stop|status}
set -euo pipefail

EVERGREEN_DIR="$HOME/.evergreen"
PID_FILE="$EVERGREEN_DIR/server.pid"
LOG_FILE="$EVERGREEN_DIR/server.log"
SERVER_SCRIPT="$(dirname "$0")/evergreen-server.py"

mkdir -p "$EVERGREEN_DIR"

case "${1:-status}" in
  start)
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
      echo "Server already running (pid $(cat "$PID_FILE"))"
      exit 1
    fi
    nohup python3 "$SERVER_SCRIPT" >> "$LOG_FILE" 2>&1 &
    echo $! > "$PID_FILE"
    echo "Server started (pid $!), logging to $LOG_FILE"
    ;;
  stop)
    if [ ! -f "$PID_FILE" ]; then
      echo "No PID file found"
      exit 1
    fi
    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
      kill "$PID"
      echo "Server stopped (pid $PID)"
    else
      echo "Process $PID not running"
    fi
    rm -f "$PID_FILE"
    ;;
  status)
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
      echo "Running (pid $(cat "$PID_FILE"))"
      echo "Log: $LOG_FILE"
      echo ""
      echo "Schedule:"
      python3 -c "
import sys; sys.path.insert(0, '$(dirname "$0")/../src')
from evergreen.db import get_connection
conn = get_connection()
for skill, interval, enabled, last in conn.execute('SELECT skill, interval_minutes, enabled, last_run_at FROM cron_jobs ORDER BY skill'):
    status = 'enabled' if enabled else 'disabled'
    last_str = last or 'never'
    print(f'  {skill}: every {interval}m ({status}), last run: {last_str}')
conn.close()
"
    else
      echo "Not running"
    fi
    ;;
  *)
    echo "Usage: $0 {start|stop|status}"
    exit 1
    ;;
esac
