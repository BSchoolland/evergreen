#!/usr/bin/env python3
"""Evergreen dashboard server — serves the web UI and JSON API."""

import json
import os
import signal
import subprocess
import sys
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from evergreen.db import epoch, get_connection, init_db

PORT = int(os.environ.get("EVERGREEN_DASHBOARD_PORT", 8080))
STATIC_DIR = Path(__file__).resolve().parent


def query_db(sql, params=()):
    conn = get_connection()
    conn.row_factory = lambda c, r: dict(zip([col[0] for col in c.description], r))
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return rows


def get_bugs():
    return query_db(
        "SELECT id, created_at, environment, error_pattern, source_query, severity, summary, "
        "probable_root_cause, verified_root_cause, verification_notes, disposition_reason, "
        "pr_url, pr_status, discord_message_id, status, occurrence_count, first_seen_at, last_seen_at "
        "FROM bugs ORDER BY created_at DESC"
    )


def get_alerts():
    return query_db(
        "SELECT id, created_at, source, source_url, cve, name, severity, "
        "affected_component, summary, impact_assessment, pr_url, pr_status, discord_message_id, status "
        "FROM security_alerts ORDER BY created_at DESC"
    )


def get_runs():
    return query_db(
        "SELECT id, started_at, finished_at, type, summary, cost, tokens, model, effort, "
        "parent_run_id, branch, pr_url "
        "FROM runs ORDER BY started_at DESC LIMIT 200"
    )


def get_cron():
    return query_db("SELECT skill, interval_minutes, enabled, last_run_at FROM cron_jobs")


def get_stats():
    conn = get_connection()
    bugs_open = conn.execute("SELECT COUNT(*) FROM bugs WHERE status NOT IN ('resolved', 'not_actionable')").fetchone()[0]
    bugs_resolved = conn.execute("SELECT COUNT(*) FROM bugs WHERE status = 'resolved'").fetchone()[0]
    alerts_actionable = conn.execute("SELECT COUNT(*) FROM security_alerts WHERE status IN ('new', 'in_progress')").fetchone()[0]
    alerts_cleared = conn.execute("SELECT COUNT(*) FROM security_alerts WHERE status IN ('not_affected', 'not_actionable', 'resolved')").fetchone()[0]
    total_runs = conn.execute("SELECT COUNT(*) FROM runs WHERE type NOT LIKE 'review:%'").fetchone()[0]
    review_runs = conn.execute("SELECT COUNT(*) FROM runs WHERE type = 'review'").fetchone()[0]
    timeouts = conn.execute("SELECT COUNT(*) FROM runs WHERE summary LIKE 'TIMEOUT%'").fetchone()[0]
    cost_total = conn.execute("SELECT COALESCE(SUM(cost), 0) FROM runs").fetchone()[0]
    cost_7d = conn.execute(
        "SELECT COALESCE(SUM(cost), 0) FROM runs WHERE started_at > ?",
        (epoch() - 7 * 86400,),
    ).fetchone()[0]
    prs_open = (
        conn.execute("SELECT COUNT(*) FROM bugs WHERE pr_url IS NOT NULL AND pr_status = 'open'").fetchone()[0]
        + conn.execute("SELECT COUNT(*) FROM security_alerts WHERE pr_url IS NOT NULL AND pr_status = 'open'").fetchone()[0]
    )
    prs_merged = (
        conn.execute("SELECT COUNT(*) FROM bugs WHERE pr_url IS NOT NULL AND pr_status = 'merged'").fetchone()[0]
        + conn.execute("SELECT COUNT(*) FROM security_alerts WHERE pr_url IS NOT NULL AND pr_status = 'merged'").fetchone()[0]
    )
    prs_closed = (
        conn.execute("SELECT COUNT(*) FROM bugs WHERE pr_url IS NOT NULL AND pr_status = 'closed'").fetchone()[0]
        + conn.execute("SELECT COUNT(*) FROM security_alerts WHERE pr_url IS NOT NULL AND pr_status = 'closed'").fetchone()[0]
    )
    conn.close()
    return {
        "bugs_open": bugs_open,
        "bugs_resolved": bugs_resolved,
        "alerts_actionable": alerts_actionable,
        "alerts_cleared": alerts_cleared,
        "total_runs": total_runs,
        "review_runs": review_runs,
        "timeouts": timeouts,
        "cost_total": round(cost_total, 2),
        "cost_7d": round(cost_7d, 2),
        "prs_open": prs_open,
        "prs_merged": prs_merged,
        "prs_closed": prs_closed,
    }


def get_daily_activity():
    return {
        "runs": query_db(
            "SELECT date(started_at, 'unixepoch', 'localtime') as day, COUNT(*) as n "
            "FROM runs WHERE started_at IS NOT NULL AND type NOT LIKE 'review:%' "
            "GROUP BY day ORDER BY day"
        ),
        "bugs": query_db(
            "SELECT date(created_at, 'unixepoch', 'localtime') as day, COUNT(*) as n "
            "FROM bugs GROUP BY day ORDER BY day"
        ),
        "prs": query_db(
            "SELECT date(created_at, 'unixepoch', 'localtime') as day, COUNT(*) as n "
            "FROM bugs WHERE pr_url IS NOT NULL "
            "GROUP BY day ORDER BY day"
        ),
        "threats": query_db(
            "SELECT date(created_at, 'unixepoch', 'localtime') as day, COUNT(*) as n "
            "FROM security_alerts GROUP BY day ORDER BY day"
        ),
    }


def sync_pr_statuses():
    conn = get_connection()
    updated = 0
    for table in ("bugs", "security_alerts"):
        rows = conn.execute(f"SELECT id, pr_url FROM {table} WHERE pr_url IS NOT NULL").fetchall()
        for row_id, pr_url in rows:
            try:
                result = subprocess.run(
                    ["gh", "pr", "view", pr_url, "--json", "state", "--jq", ".state"],
                    capture_output=True, text=True, timeout=10,
                )
                if result.returncode == 0:
                    state = result.stdout.strip().lower()
                    if state in ("open", "merged", "closed"):
                        conn.execute(f"UPDATE {table} SET pr_status = ? WHERE id = ?", (state, row_id))
                        updated += 1
            except (subprocess.TimeoutExpired, FileNotFoundError):
                pass
    conn.commit()
    conn.close()
    return {"updated": updated}


API_ROUTES = {
    "/api/bugs": get_bugs,
    "/api/alerts": get_alerts,
    "/api/runs": get_runs,
    "/api/cron": get_cron,
    "/api/stats": get_stats,
    "/api/daily-activity": get_daily_activity,
    "/api/sync-pr-statuses": sync_pr_statuses,
}


class DashboardHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    def do_GET(self):
        path = urlparse(self.path).path

        if path in API_ROUTES:
            data = API_ROUTES[path]()
            body = json.dumps(data).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", len(body))
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/":
            self.path = "/dashboard.html"

        super().do_GET()

    def log_message(self, format, *args):
        pass


def get_tailscale_ip():
    try:
        result = subprocess.run(["tailscale", "ip", "-4"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def main():
    conn = init_db()
    conn.close()

    signal.signal(signal.SIGINT, lambda *_: sys.exit(0))
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

    server = HTTPServer(("0.0.0.0", PORT), DashboardHandler)
    ts_ip = get_tailscale_ip()

    print(f"Dashboard running on http://localhost:{PORT}")
    if ts_ip:
        print(f"Tailscale: http://{ts_ip}:{PORT}")

    server.serve_forever()


if __name__ == "__main__":
    main()
