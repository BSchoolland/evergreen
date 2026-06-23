"""Monitoring plane: deterministic uptime + SSL probing for every active project.

This is CODE, not an agent skill — it runs cheaply and often, in the watchdog's
tick loop, for every active project that has a base_url. It records uptime_checks
rows, maintains the project_status summary, and queues a Discord alert (via the
discord_messages outbound table the bot drains) when a project flips up<->down.

The agent reads this data through /uptime-status-evergreen; it never polls itself.
"""

import socket
import ssl
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from urllib.parse import urlparse

from evergreen.db import (
    epoch,
    get_connection,
    project_alert_channel,
    project_config_value,
)

DEFAULT_INTERVAL_SEC = 300
DEFAULT_FAILURE_THRESHOLD = 3
REQUEST_TIMEOUT = 15
USER_AGENT = "evergreen-uptime/1.0 (+https://github.com/BSchoolland/evergreen)"


def _ssl_expiry_days(host: str, port: int = 443) -> int | None:
    """Days until the TLS cert for host expires, or None if it can't be read."""
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((host, port), timeout=REQUEST_TIMEOUT) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as ssock:
                cert = ssock.getpeercert()
        not_after = cert.get("notAfter")
        if not not_after:
            return None
        expires = datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
        return int((expires - datetime.now(timezone.utc)).total_seconds() // 86400)
    except Exception:
        return None


def check_url(url: str) -> dict:
    """Probe a URL once. up == the server responded with HTTP < 500. A connection
    error, timeout, or 5xx is down. SSL expiry is read for https URLs."""
    parsed = urlparse(url)
    started = time.monotonic()
    result = {
        "is_up": 0, "status_code": None, "response_time_ms": None,
        "ssl_expiry_days": None, "error": None,
    }
    req = urllib.request.Request(url, method="GET", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            resp.read(2048)
            result["status_code"] = resp.status
            result["is_up"] = 1 if resp.status < 500 else 0
    except urllib.error.HTTPError as e:
        # The server answered — that counts as up unless it's a 5xx.
        result["status_code"] = e.code
        result["is_up"] = 1 if e.code < 500 else 0
    except (urllib.error.URLError, socket.timeout, ConnectionError, OSError) as e:
        result["error"] = str(getattr(e, "reason", e))[:300]
        result["is_up"] = 0
    result["response_time_ms"] = int((time.monotonic() - started) * 1000)

    if parsed.scheme == "https" and parsed.hostname:
        result["ssl_expiry_days"] = _ssl_expiry_days(parsed.hostname, parsed.port or 443)
    return result


def _queue_discord(conn, channel_id: str | None, content: str):
    if not channel_id:
        return
    conn.execute(
        "INSERT INTO discord_messages (channel_id, direction, content) VALUES (?, 'outbound', ?)",
        (channel_id, content),
    )


def record_check(conn, project, result: dict):
    """Write the check row, update project_status, and queue an alert on a
    confirmed up<->down transition. project_status.is_up is the CONFIRMED state
    (after failure_threshold consecutive failures), not the instantaneous one."""
    pid = project["id"]
    now = epoch()
    conn.execute(
        "INSERT INTO uptime_checks (project_id, checked_at, is_up, status_code, response_time_ms, ssl_expiry_days, error) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (pid, now, result["is_up"], result["status_code"], result["response_time_ms"],
         result["ssl_expiry_days"], result["error"]),
    )

    row = conn.execute(
        "SELECT is_up, consecutive_failures FROM project_status WHERE project_id = ?", (pid,)
    ).fetchone()
    prev_confirmed = row[0] if row else None
    consec = row[1] if row else 0

    threshold = int(project_config_value(pid, "failure_threshold", DEFAULT_FAILURE_THRESHOLD))
    transition = None
    confirmed = prev_confirmed

    if result["is_up"]:
        consec = 0
        if prev_confirmed == 0:
            transition = "up"
        confirmed = 1
    else:
        consec += 1
        if prev_confirmed != 0 and consec >= threshold:
            transition = "down"
            confirmed = 0
        elif prev_confirmed is None:
            confirmed = 1  # first-ever check that failed but below threshold: stay "unknown-ish up"

    conn.execute(
        """INSERT INTO project_status
             (project_id, is_up, consecutive_failures, last_status_code, last_checked_at,
              last_response_time_ms, ssl_expiry_days, last_transition_at, last_alert_sent_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(project_id) DO UPDATE SET
             is_up=excluded.is_up,
             consecutive_failures=excluded.consecutive_failures,
             last_status_code=excluded.last_status_code,
             last_checked_at=excluded.last_checked_at,
             last_response_time_ms=excluded.last_response_time_ms,
             ssl_expiry_days=excluded.ssl_expiry_days,
             last_transition_at=COALESCE(excluded.last_transition_at, project_status.last_transition_at),
             last_alert_sent_at=COALESCE(excluded.last_alert_sent_at, project_status.last_alert_sent_at)""",
        (pid, confirmed if confirmed is not None else 1, consec, result["status_code"], now,
         result["response_time_ms"], result["ssl_expiry_days"],
         now if transition else None, now if transition else None),
    )

    if transition == "down":
        detail = result["error"] or f"HTTP {result['status_code']}"
        _queue_discord(conn, project_alert_channel(pid),
                       f"\U0001f534 **{project['name']}** is DOWN — {detail}\n{project['base_url']}")
    elif transition == "up":
        rt = result["response_time_ms"]
        _queue_discord(conn, project_alert_channel(pid),
                       f"\U0001f7e2 **{project['name']}** recovered — HTTP {result['status_code']} ({rt}ms)\n{project['base_url']}")
    return transition


def _due(conn, pid: int, interval_sec: int) -> bool:
    row = conn.execute(
        "SELECT last_checked_at FROM project_status WHERE project_id = ?", (pid,)
    ).fetchone()
    if not row or row[0] is None:
        return True
    return epoch() - row[0] >= interval_sec


def run_sweep(log=None):
    """Probe every active project whose uptime check is due. Called each watchdog
    tick; cheap enough to run every minute (it self-throttles per project)."""
    from evergreen.db import list_projects
    conn = get_connection()
    checked = 0
    for project in list_projects(include_inactive=False):
        url = project.get("base_url")
        if not url:
            continue
        if project["config"].get("uptime_enabled") is False:
            continue
        interval = int(project_config_value(project["id"], "uptime_interval_sec", DEFAULT_INTERVAL_SEC))
        if not _due(conn, project["id"], interval):
            continue
        result = check_url(url)
        transition = record_check(conn, project, result)
        conn.commit()
        checked += 1
        if log and transition:
            log.info("uptime: %s -> %s", project["slug"], transition)
    conn.close()
    return checked
