#!/usr/bin/env python3
"""Export Evergreen DB to a Lakebed capsule and deploy it."""

import base64
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from evergreen.db import get_connection

LAKEBED_DIR = Path(__file__).resolve().parent.parent / "lakebed"
DATA_FILE = LAKEBED_DIR / "client" / "data.ts"

# Most-recent rows kept for alerts/runs (override for tuning via env). The cap
# exists because the Lakebed deploy request is limited to 2 MiB; with the inline
# sourcemap stripped from the bundle (see deploy()), 700 rows each lands the
# request around ~1.3-1.8 MiB with headroom. Aggregate stats below still count the
# full tables. Bugs are kept in full — there are far fewer of them.
ROW_LIMIT = int(os.environ.get("LAKEBED_ROW_LIMIT", "700"))

DEPLOY_META = LAKEBED_DIR / ".lakebed" / "deploy.json"
LAKEBED_VERSION = "0.0.25"
MAX_REQUEST_BYTES = 2097152
SOURCEMAP_MARKER = b"//# sourceMappingURL=data:application/json;base64,"


def query(sql, params=()):
    conn = get_connection()
    conn.row_factory = lambda c, r: dict(zip([col[0] for col in c.description], r))
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return rows


def scalar(sql):
    conn = get_connection()
    val = conn.execute(sql).fetchone()[0]
    conn.close()
    return val


def export_data():
    bugs = query(
        "SELECT id, created_at, environment, severity, summary, error_pattern, source_query, "
        "probable_root_cause, verified_root_cause, verification_notes, disposition_reason, "
        "pr_url, pr_status, status, occurrence_count, first_seen_at, last_seen_at "
        "FROM bugs ORDER BY created_at DESC"
    )
    # alerts/runs are capped to the most recent ROW_LIMIT rows (see note above).
    alerts = query(
        "SELECT id, created_at, source, source_url, cve, name, severity, "
        "affected_component, summary, impact_assessment, pr_url, pr_status, status "
        "FROM security_alerts ORDER BY created_at DESC LIMIT ?",
        (ROW_LIMIT,),
    )
    runs = query(
        "SELECT id, started_at, finished_at, type, summary, cost, tokens, model, effort "
        "FROM runs ORDER BY started_at DESC LIMIT ?",
        (ROW_LIMIT,),
    )
    cost_by_skill = query(
        "SELECT replace(type, 'cron:', '') as skill, "
        "ROUND(SUM(cost), 2) as cost, COUNT(*) as runs, ROUND(AVG(cost), 3) as avg_cost "
        "FROM runs WHERE cost IS NOT NULL GROUP BY skill ORDER BY cost DESC"
    )

    conn = get_connection()
    stats = {
        "bugs_open": conn.execute("SELECT COUNT(*) FROM bugs WHERE status NOT IN ('resolved','not_actionable')").fetchone()[0],
        "bugs_resolved": conn.execute("SELECT COUNT(*) FROM bugs WHERE status='resolved'").fetchone()[0],
        "alerts_actionable": conn.execute("SELECT COUNT(*) FROM security_alerts WHERE status IN ('new','in_progress')").fetchone()[0],
        "alerts_cleared": conn.execute("SELECT COUNT(*) FROM security_alerts WHERE status IN ('not_affected','not_actionable','resolved')").fetchone()[0],
        "total_runs": conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0],
        "timeouts": conn.execute("SELECT COUNT(*) FROM runs WHERE summary LIKE 'TIMEOUT%'").fetchone()[0],
        "cost_total": round(conn.execute("SELECT COALESCE(SUM(cost), 0) FROM runs").fetchone()[0], 2),
        "cost_7d": round(conn.execute(
            "SELECT COALESCE(SUM(cost), 0) FROM runs "
            "WHERE started_at > cast(strftime('%s','now') as integer) - 604800"
        ).fetchone()[0], 2),
        "prs_open": (
            conn.execute("SELECT COUNT(*) FROM bugs WHERE pr_url IS NOT NULL AND pr_status='open'").fetchone()[0]
            + conn.execute("SELECT COUNT(*) FROM security_alerts WHERE pr_url IS NOT NULL AND pr_status='open'").fetchone()[0]
        ),
        "prs_merged": (
            conn.execute("SELECT COUNT(*) FROM bugs WHERE pr_url IS NOT NULL AND pr_status='merged'").fetchone()[0]
            + conn.execute("SELECT COUNT(*) FROM security_alerts WHERE pr_url IS NOT NULL AND pr_status='merged'").fetchone()[0]
        ),
        "prs_closed": (
            conn.execute("SELECT COUNT(*) FROM bugs WHERE pr_url IS NOT NULL AND pr_status='closed'").fetchone()[0]
            + conn.execute("SELECT COUNT(*) FROM security_alerts WHERE pr_url IS NOT NULL AND pr_status='closed'").fetchone()[0]
        ),
    }
    conn.close()

    daily = {
        "runs": query("SELECT date(started_at,'unixepoch','localtime') as day, COUNT(*) as n FROM runs WHERE started_at IS NOT NULL GROUP BY day ORDER BY day"),
        "bugs": query("SELECT date(created_at,'unixepoch','localtime') as day, COUNT(*) as n FROM bugs GROUP BY day ORDER BY day"),
        "prs": query("SELECT date(created_at,'unixepoch','localtime') as day, COUNT(*) as n FROM bugs WHERE pr_url IS NOT NULL GROUP BY day ORDER BY day"),
        "threats": query("SELECT date(created_at,'unixepoch','localtime') as day, COUNT(*) as n FROM security_alerts GROUP BY day ORDER BY day"),
    }

    from datetime import datetime
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    return bugs, alerts, runs, stats, daily, cost_by_skill, now


TYPES = """\
// Generated by deploy-lakebed.py — do not edit manually

export type Bug = {
  id: number;
  created_at: number;
  environment: string;
  severity: string;
  summary: string;
  error_pattern: string | null;
  source_query: string | null;
  probable_root_cause: string | null;
  verified_root_cause: string | null;
  verification_notes: string | null;
  disposition_reason: string | null;
  pr_url: string | null;
  pr_status: string | null;
  status: string;
  occurrence_count: number;
  first_seen_at: number;
  last_seen_at: number;
};

export type Alert = {
  id: number;
  created_at: number;
  source: string;
  source_url: string | null;
  cve: string | null;
  name: string | null;
  severity: string;
  affected_component: string | null;
  summary: string;
  impact_assessment: string | null;
  pr_url: string | null;
  pr_status: string | null;
  status: string;
};

export type Run = {
  id: number;
  started_at: number;
  finished_at: number | null;
  type: string;
  summary: string | null;
  cost: number | null;
  tokens: number | null;
  model: string | null;
  effort: string | null;
};

export type Stats = {
  bugs_open: number;
  bugs_resolved: number;
  alerts_actionable: number;
  alerts_cleared: number;
  total_runs: number;
  timeouts: number;
  cost_total: number;
  cost_7d: number;
  prs_open: number;
  prs_merged: number;
  prs_closed: number;
};

export type CostBySkill = {
  skill: string;
  cost: number;
  runs: number;
  avg_cost: number;
};

export type DailyEntry = { day: string; n: number };

export type DailyActivity = {
  runs: DailyEntry[];
  bugs: DailyEntry[];
  prs: DailyEntry[];
  threats: DailyEntry[];
};
"""


def write_data_ts(bugs, alerts, runs, stats, daily, cost_by_skill, now):
    lines = [TYPES]
    lines.append(f"export const bugs: Bug[] = {json.dumps(bugs, separators=(',', ':'))};")
    lines.append(f"export const alerts: Alert[] = {json.dumps(alerts, separators=(',', ':'))};")
    lines.append(f"export const runs: Run[] = {json.dumps(runs, separators=(',', ':'))};")
    lines.append(f"export const stats: Stats = {json.dumps(stats, separators=(',', ':'))};")
    lines.append(f"export const daily: DailyActivity = {json.dumps(daily, separators=(',', ':'))};")
    lines.append(f"export const costBySkill: CostBySkill[] = {json.dumps(cost_by_skill, separators=(',', ':'))};")
    lines.append(f'export const lastUpdated = "{now}";')
    lines.append("")
    DATA_FILE.write_text("\n".join(lines))
    print(f"Wrote {DATA_FILE} ({DATA_FILE.stat().st_size} bytes)")


def git_commit():
    subprocess.run(["git", "add", "-A"], cwd=LAKEBED_DIR, check=True)
    result = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=LAKEBED_DIR)
    if result.returncode == 0:
        print("No changes to commit")
        return False
    subprocess.run(
        ["git", "commit", "-m", "Update dashboard data"],
        cwd=LAKEBED_DIR, check=True, capture_output=True,
    )
    print("Committed changes")
    return True


def strip_inline_sourcemap(client_bundle_b64):
    """Drop esbuild's inline sourcemap from a base64-encoded client bundle.

    Lakebed v0.0.25 hardcodes `sourcemap: "inline"` in its esbuild config with no
    flag to disable it, so ~65% of every deployed bundle is a base64 sourcemap that
    blows the 2 MiB request limit. The map is purely a debug aid for the prod
    dashboard, so we cut it before upload. Returns (stripped_b64, bytes_saved).
    """
    raw = base64.b64decode(client_bundle_b64)
    idx = raw.find(SOURCEMAP_MARKER)
    if idx == -1:
        return client_bundle_b64, 0
    code = raw[:idx].rstrip()
    return base64.b64encode(code).decode(), len(raw) - len(code)


def deploy():
    """Build the capsule, strip the inline sourcemap, and upload it ourselves.

    We can't use `lakebed deploy` directly because it bundles a giant inline
    sourcemap (see strip_inline_sourcemap). Instead we run `lakebed build` to get
    the envelope, strip the map, and replicate the CLI's own PUT to the deploy API.
    """
    meta = json.loads(DEPLOY_META.read_text())
    api, deploy_id, claim_token = meta["api"], meta["deployId"], meta["claimToken"]

    print("Building Lakebed capsule...")
    with tempfile.NamedTemporaryFile("r", suffix=".json", delete=False) as tmp:
        envelope_path = tmp.name
    result = subprocess.run(
        ["npx", "lakebed", "build", "--out", envelope_path],
        cwd=LAKEBED_DIR, capture_output=True, text=True, timeout=120,
    )
    if result.returncode != 0:
        print(result.stdout)
        print(result.stderr, file=sys.stderr)
        print(f"Build failed (exit {result.returncode})", file=sys.stderr)
        sys.exit(1)

    envelope = json.loads(Path(envelope_path).read_text())
    os.unlink(envelope_path)
    envelope["clientBundle"], saved = strip_inline_sourcemap(envelope["clientBundle"])
    print(f"Stripped inline sourcemap ({saved} bytes from bundle)")

    # The artifact carries a hash + byte count of the client bundle that the API
    # validates against the upload; recompute both now that the bundle is smaller.
    stripped_raw = base64.b64decode(envelope["clientBundle"])
    envelope["artifact"]["client"]["bundleHash"] = "sha256:" + hashlib.sha256(stripped_raw).hexdigest()
    envelope["artifact"]["client"]["bytes"] = len(stripped_raw)

    body = json.dumps({
        "artifact": envelope["artifact"],
        "clientBundle": envelope["clientBundle"],
        "clientVersion": LAKEBED_VERSION,
    }).encode()
    print(f"Deploy request: {len(body)} bytes ({len(body)/1048576:.2f} MiB) of {MAX_REQUEST_BYTES/1048576:.2f} MiB limit")
    if len(body) > MAX_REQUEST_BYTES:
        print(f"Request exceeds {MAX_REQUEST_BYTES} bytes — lower LAKEBED_ROW_LIMIT (currently {ROW_LIMIT})", file=sys.stderr)
        sys.exit(1)

    print("Deploying to Lakebed...")
    req = urllib.request.Request(
        f"{api}/v1/deploys/{deploy_id}",
        data=body, method="PUT",
        headers={"Authorization": f"Bearer {claim_token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            deployed = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        if e.code in (404, 410):
            print(
                "Deploy target is gone (404/410). Recreate it with "
                "`cd lakebed && npx lakebed deploy`, then re-run this script.",
                file=sys.stderr,
            )
        print(f"Deploy failed ({e.code}): {detail}", file=sys.stderr)
        sys.exit(1)

    # Keep deploy.json fresh (preserve a rotated claim token if the API returns one).
    meta["updatedAt"] = deployed.get("updatedAt", meta.get("updatedAt"))
    meta["url"] = deployed.get("url", meta.get("url"))
    if deployed.get("claimToken"):
        meta["claimToken"] = deployed["claimToken"]
    DEPLOY_META.write_text(json.dumps(meta, indent=2) + "\n")
    print(f"Deployed: {deployed.get('url', meta.get('url'))}")


def main():
    print("Exporting Evergreen DB...")
    bugs, alerts, runs, stats, daily, cost_by_skill, now = export_data()
    print(f"  {len(bugs)} bugs, {len(alerts)} alerts, {len(runs)} runs")
    write_data_ts(bugs, alerts, runs, stats, daily, cost_by_skill, now)
    changed = git_commit()
    if changed:
        deploy()
    else:
        print("Data unchanged, skipping deploy")


if __name__ == "__main__":
    main()
