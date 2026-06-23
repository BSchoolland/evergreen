#!/usr/bin/env python3
"""Evergreen dashboard server — serves the web UI and JSON API."""

import json
import os
import signal
import subprocess
import sys
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from evergreen.db import epoch, get_connection, init_db, list_projects

PORT = int(os.environ.get("EVERGREEN_DASHBOARD_PORT", 8080))
STATIC_DIR = Path(__file__).resolve().parent


def query_db(sql, params=()):
    conn = get_connection()
    conn.row_factory = lambda c, r: dict(zip([col[0] for col in c.description], r))
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return rows


def _scope(clause, project_id, sql_prefix=" WHERE"):
    """Return (sql_fragment, params) appending a project_id filter when one is set."""
    if project_id is None:
        return "", ()
    return f"{sql_prefix} {clause}", (project_id,)


def get_projects(project_id=None):
    return [
        {k: p[k] for k in ("id", "slug", "name", "type", "status", "base_url", "depth_tier")}
        for p in list_projects()
    ]


def get_bugs(project_id=None):
    where, params = _scope("project_id = ?", project_id)
    return query_db(
        "SELECT id, created_at, environment, error_pattern, source_query, severity, summary, "
        "probable_root_cause, verified_root_cause, verification_notes, disposition_reason, "
        "pr_url, pr_status, discord_message_id, status, occurrence_count, first_seen_at, last_seen_at "
        f"FROM bugs{where} ORDER BY created_at DESC",
        params,
    )


def get_alerts(project_id=None):
    where, params = _scope("project_id = ?", project_id)
    return query_db(
        "SELECT id, created_at, source, source_url, cve, name, severity, "
        "affected_component, summary, impact_assessment, pr_url, pr_status, discord_message_id, status "
        f"FROM security_alerts{where} ORDER BY created_at DESC",
        params,
    )


def get_runs(project_id=None):
    where, params = _scope("project_id = ?", project_id)
    return query_db(
        "SELECT id, started_at, finished_at, type, summary, cost, tokens, model, effort, "
        "parent_run_id, branch, pr_url "
        f"FROM runs{where} ORDER BY started_at DESC LIMIT 200",
        params,
    )


def get_cron(project_id=None):
    where, params = _scope("project_id = ?", project_id)
    return query_db(
        f"SELECT skill, interval_minutes, enabled, last_run_at FROM cron_jobs{where}",
        params,
    )


def _project_overview(conn, now):
    rows = []
    day_ago = now - 86400
    week_ago = now - 7 * 86400
    for p in conn.execute(
        "SELECT id, name, type, status FROM projects ORDER BY id"
    ).fetchall():
        pid, name, ptype, status = p
        open_bugs = conn.execute(
            "SELECT COUNT(*) FROM bugs WHERE project_id = ? AND status NOT IN ('resolved', 'not_actionable')",
            (pid,),
        ).fetchone()[0]
        open_alerts = conn.execute(
            "SELECT COUNT(*) FROM security_alerts WHERE project_id = ? AND status IN ('new', 'in_progress')",
            (pid,),
        ).fetchone()[0]
        ps = conn.execute(
            "SELECT is_up, last_checked_at FROM project_status WHERE project_id = ?", (pid,)
        ).fetchone()
        is_up, last_checked_at = (ps[0], ps[1]) if ps else (None, None)
        up_row = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(is_up), 0) FROM uptime_checks "
            "WHERE project_id = ? AND checked_at > ?",
            (pid, day_ago),
        ).fetchone()
        total_checks, up_checks = up_row[0], up_row[1]
        uptime_pct = round(100.0 * up_checks / total_checks, 1) if total_checks else None
        spend_7d = conn.execute(
            "SELECT COALESCE(SUM(cost), 0) FROM runs WHERE project_id = ? AND started_at > ?",
            (pid, week_ago),
        ).fetchone()[0]
        rows.append({
            "id": pid,
            "name": name,
            "type": ptype,
            "status": status,
            "open_bugs": open_bugs,
            "open_alerts": open_alerts,
            "is_up": is_up,
            "last_checked_at": last_checked_at,
            "uptime_pct": uptime_pct,
            "spend_7d": round(spend_7d, 2),
        })
    return rows


def get_stats(project_id=None):
    conn = get_connection()
    pf, pp = (" AND project_id = ?", (project_id,)) if project_id is not None else ("", ())

    def count(sql):
        return conn.execute(sql, pp).fetchone()[0]

    bugs_open = count(f"SELECT COUNT(*) FROM bugs WHERE status NOT IN ('resolved', 'not_actionable'){pf}")
    bugs_resolved = count(f"SELECT COUNT(*) FROM bugs WHERE status = 'resolved'{pf}")
    alerts_actionable = count(f"SELECT COUNT(*) FROM security_alerts WHERE status IN ('new', 'in_progress'){pf}")
    alerts_cleared = count(f"SELECT COUNT(*) FROM security_alerts WHERE status IN ('not_affected', 'not_actionable', 'resolved'){pf}")
    total_runs = count(f"SELECT COUNT(*) FROM runs WHERE type NOT LIKE 'review:%'{pf}")
    review_runs = count(f"SELECT COUNT(*) FROM runs WHERE type = 'review'{pf}")
    timeouts = count(f"SELECT COUNT(*) FROM runs WHERE summary LIKE 'TIMEOUT%'{pf}")
    cost_total = count(f"SELECT COALESCE(SUM(cost), 0) FROM runs WHERE 1=1{pf}")
    cost_7d = conn.execute(
        f"SELECT COALESCE(SUM(cost), 0) FROM runs WHERE started_at > ?{pf}",
        (epoch() - 7 * 86400, *pp),
    ).fetchone()[0]
    prs_open = (
        count(f"SELECT COUNT(*) FROM bugs WHERE pr_url IS NOT NULL AND pr_status = 'open'{pf}")
        + count(f"SELECT COUNT(*) FROM security_alerts WHERE pr_url IS NOT NULL AND pr_status = 'open'{pf}")
    )
    prs_merged = (
        count(f"SELECT COUNT(*) FROM bugs WHERE pr_url IS NOT NULL AND pr_status = 'merged'{pf}")
        + count(f"SELECT COUNT(*) FROM security_alerts WHERE pr_url IS NOT NULL AND pr_status = 'merged'{pf}")
    )
    prs_closed = (
        count(f"SELECT COUNT(*) FROM bugs WHERE pr_url IS NOT NULL AND pr_status = 'closed'{pf}")
        + count(f"SELECT COUNT(*) FROM security_alerts WHERE pr_url IS NOT NULL AND pr_status = 'closed'{pf}")
    )
    overview = _project_overview(conn, epoch())
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
        "overview": overview,
    }


def get_uptime(project_id=None, hours=24):
    now = epoch()
    since = now - int(hours) * 3600
    chk_where = "checked_at > ?"
    chk_params = [since]
    if project_id is not None:
        chk_where += " AND project_id = ?"
        chk_params.append(project_id)
    checks = query_db(
        "SELECT project_id, checked_at, is_up, status_code, response_time_ms, ssl_expiry_days, error "
        f"FROM uptime_checks WHERE {chk_where} ORDER BY checked_at",
        tuple(chk_params),
    )
    st_where, st_params = _scope("project_id = ?", project_id)
    status = query_db(
        "SELECT project_id, is_up, consecutive_failures, last_status_code, last_checked_at, "
        "last_response_time_ms, ssl_expiry_days, last_transition_at "
        f"FROM project_status{st_where}",
        st_params,
    )
    return {"checks": checks, "status": status}


def get_budget(project_id=None):
    conn = get_connection()
    conn.row_factory = lambda c, r: dict(zip([col[0] for col in c.description], r))
    now = epoch()
    day = 86400

    # Month-to-date boundary (local time, start of month).
    mtd_start = conn.execute(
        "SELECT cast(strftime('%s', strftime('%Y-%m-01', 'now', 'localtime')) as integer) AS s"
    ).fetchone()["s"]

    windows = {"7d": now - 7 * day, "30d": now - 30 * day, "mtd": mtd_start}

    by_project = {}
    for p in conn.execute("SELECT id, name FROM projects ORDER BY id").fetchall():
        pid, name = p["id"], p["name"]
        entry = {"id": pid, "name": name}
        for label, since in windows.items():
            entry[label] = round(
                conn.execute(
                    "SELECT COALESCE(SUM(cost), 0) AS c FROM runs WHERE project_id = ? AND started_at > ?",
                    (pid, since),
                ).fetchone()["c"],
                2,
            )
        by_project[pid] = entry

    pf, pp = (" AND project_id = ?", (project_id,)) if project_id is not None else ("", ())
    by_skill = conn.execute(
        "SELECT type, COALESCE(SUM(cost), 0) AS cost, COUNT(*) AS runs FROM runs "
        f"WHERE started_at > ? AND type NOT LIKE 'review:%'{pf} GROUP BY type ORDER BY cost DESC",
        (now - 30 * day, *pp),
    ).fetchall()
    for s in by_skill:
        s["cost"] = round(s["cost"], 2)

    totals = {}
    for label, since in windows.items():
        totals[label] = round(
            conn.execute(
                "SELECT COALESCE(SUM(cost), 0) AS c FROM runs WHERE started_at > ?", (since,)
            ).fetchone()["c"],
            2,
        )
    conn.close()
    return {
        "by_project": list(by_project.values()),
        "by_skill": by_skill,
        "totals": totals,
    }


def get_daily_activity(project_id=None):
    pf, pp = (" AND project_id = ?", (project_id,)) if project_id is not None else ("", ())
    return {
        "runs": query_db(
            "SELECT date(started_at, 'unixepoch', 'localtime') as day, COUNT(*) as n "
            f"FROM runs WHERE started_at IS NOT NULL AND type NOT LIKE 'review:%'{pf} "
            "GROUP BY day ORDER BY day",
            pp,
        ),
        "bugs": query_db(
            "SELECT date(created_at, 'unixepoch', 'localtime') as day, COUNT(*) as n "
            f"FROM bugs WHERE 1=1{pf} GROUP BY day ORDER BY day",
            pp,
        ),
        "prs": query_db(
            "SELECT date(created_at, 'unixepoch', 'localtime') as day, COUNT(*) as n "
            f"FROM bugs WHERE pr_url IS NOT NULL{pf} "
            "GROUP BY day ORDER BY day",
            pp,
        ),
        "threats": query_db(
            "SELECT date(created_at, 'unixepoch', 'localtime') as day, COUNT(*) as n "
            f"FROM security_alerts WHERE 1=1{pf} GROUP BY day ORDER BY day",
            pp,
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


# Routes that accept a project_id filter parsed from the query string.
API_ROUTES = {
    "/api/projects": get_projects,
    "/api/bugs": get_bugs,
    "/api/alerts": get_alerts,
    "/api/runs": get_runs,
    "/api/cron": get_cron,
    "/api/stats": get_stats,
    "/api/uptime": get_uptime,
    "/api/budget": get_budget,
    "/api/daily-activity": get_daily_activity,
}

# Routes that take no project scope.
API_ROUTES_PLAIN = {
    "/api/sync-pr-statuses": sync_pr_statuses,
}


def _parse_project_id(qs):
    val = qs.get("project_id", [None])[0]
    if val and val.isdigit():
        return int(val)
    return None


class DashboardHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path in API_ROUTES or path in API_ROUTES_PLAIN:
            if path in API_ROUTES_PLAIN:
                data = API_ROUTES_PLAIN[path]()
            else:
                qs = parse_qs(parsed.query)
                project_id = _parse_project_id(qs)
                if path == "/api/uptime":
                    hours = qs.get("hours", ["24"])[0]
                    hours = int(hours) if hours.isdigit() else 24
                    data = get_uptime(project_id=project_id, hours=hours)
                else:
                    data = API_ROUTES[path](project_id=project_id)
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
