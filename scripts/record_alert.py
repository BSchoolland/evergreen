#!/usr/bin/env python3
"""Record a security alert to the Evergreen database. Designed to be called by Claude Code skills."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from evergreen.db import current_project_id, get_connection


def resolve_project_id(explicit=None):
    return explicit if explicit is not None else (current_project_id() or 1)


def find_duplicate(conn, project_id, source, cve, source_url):
    """Existing alert id matching this one's identity, or None.

    The UNIQUE(project_id, source, cve, source_url) table constraint can't do this
    on its own: SQL treats NULL as distinct, so rows with no CVE never collide.
    Most advisories (bun audit, GHSA-only) have no CVE, so they re-file every run.

    With neither a CVE nor a source_url there is no stable identity to match on --
    dedup would collapse every alert from that source into one, so don't try.
    """
    if cve is None and source_url is None:
        return None
    row = conn.execute(
        """SELECT id FROM security_alerts
           WHERE project_id = ? AND source = ? AND cve IS ? AND source_url IS ?""",
        (project_id, source, cve, source_url),
    ).fetchone()
    return row[0] if row else None


def record_alert(
    source,
    severity,
    summary,
    source_url=None,
    article_url=None,
    cve=None,
    name=None,
    affected_component=None,
    impact_assessment=None,
    pr_url=None,
    pr_status=None,
    discord_message_id=None,
    project_id=None,
):
    project_id = resolve_project_id(project_id)
    conn = get_connection()
    try:
        existing = find_duplicate(conn, project_id, source, cve, source_url)
        if existing is not None:
            print(f"Skipped (duplicate of #{existing}): {summary}")
            return None
        conn.execute(
            """INSERT INTO security_alerts
               (project_id, source, source_url, article_url, cve, name, severity, affected_component, summary, impact_assessment, pr_url, pr_status, discord_message_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (project_id, source, source_url, article_url, cve, name, severity, affected_component, summary, impact_assessment, pr_url, pr_status, discord_message_id),
        )
        conn.commit()
        row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        print(f"Recorded alert #{row_id}: {summary}")
        return row_id
    except Exception as e:
        if "UNIQUE constraint failed" in str(e):
            print(f"Skipped (duplicate): {summary}")
            return None
        raise
    finally:
        conn.close()


def record_batch(alerts, project_id=None):
    """Insert multiple alerts from a list of dicts. Returns (inserted, skipped) counts."""
    project_id = resolve_project_id(project_id)
    conn = get_connection()
    inserted, skipped = 0, 0
    try:
        for a in alerts:
            try:
                existing = find_duplicate(
                    conn, project_id, a["source"], a.get("cve"), a.get("source_url")
                )
                if existing is not None:
                    skipped += 1
                    print(f"  Skipped (duplicate of #{existing}): {a['summary']}")
                    continue
                conn.execute(
                    """INSERT INTO security_alerts
                       (project_id, source, source_url, article_url, cve, name, severity, affected_component, summary, impact_assessment, pr_url, pr_status, discord_message_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        project_id,
                        a["source"],
                        a.get("source_url"),
                        a.get("article_url"),
                        a.get("cve"),
                        a.get("name"),
                        a["severity"],
                        a.get("affected_component"),
                        a["summary"],
                        a.get("impact_assessment"),
                        a.get("pr_url"),
                        a.get("pr_status"),
                        a.get("discord_message_id"),
                    ),
                )
                inserted += 1
                print(f"  Recorded: {a['summary']}")
            except Exception as e:
                if "UNIQUE constraint failed" in str(e):
                    skipped += 1
                    print(f"  Skipped (duplicate): {a['summary']}")
                else:
                    raise
        conn.commit()
    finally:
        conn.close()
    print(f"\n{inserted} recorded, {skipped} skipped (duplicates)")
    return inserted, skipped


def list_alerts(limit=20, project_id=None, all_projects=False):
    conn = get_connection()
    where = ""
    params = []
    if not all_projects:
        where = "WHERE project_id = ?"
        params.append(resolve_project_id(project_id))
    params.append(limit)
    rows = conn.execute(
        f"""SELECT id, created_at, source, cve, name, severity, affected_component, summary, status
           FROM security_alerts {where} ORDER BY created_at DESC LIMIT ?""",
        params,
    ).fetchall()
    conn.close()
    if not rows:
        print("No alerts recorded.")
        return
    for row in rows:
        id, ts, src, cve, name, sev, comp, summary, status = row
        ident = " / ".join(filter(None, [cve, name]))
        ident_str = f" ({ident})" if ident else ""
        comp_str = f" [{comp}]" if comp else ""
        print(f"#{id} [{sev}] {summary}{ident_str}{comp_str} — {status} ({ts})")


def main():
    parser = argparse.ArgumentParser(description="Record security alerts to Evergreen DB")
    sub = parser.add_subparsers(dest="command")

    add = sub.add_parser("add", help="Add a single alert")
    add.add_argument("--source", required=True)
    add.add_argument("--severity", required=True, choices=["critical", "high", "medium", "low", "info"])
    add.add_argument("--summary", required=True)
    add.add_argument("--source-url")
    add.add_argument("--article-url")
    add.add_argument("--cve")
    add.add_argument("--name")
    add.add_argument("--affected-component")
    add.add_argument("--impact-assessment")
    add.add_argument("--pr-url")
    add.add_argument("--pr-status", choices=["open", "merged", "closed"])
    add.add_argument("--discord-message-id")
    add.add_argument("--project-id", type=int)

    batch = sub.add_parser("batch", help="Add alerts from JSON file or stdin")
    batch.add_argument("file", nargs="?", default="-")
    batch.add_argument("--project-id", type=int)

    ls = sub.add_parser("list", help="List recent alerts")
    ls.add_argument("--limit", type=int, default=20)
    ls.add_argument("--project-id", type=int)
    ls.add_argument("--all", action="store_true", help="Show alerts across all projects")

    args = parser.parse_args()

    if args.command == "add":
        record_alert(
            source=args.source,
            severity=args.severity,
            summary=args.summary,
            source_url=args.source_url,
            article_url=args.article_url,
            cve=args.cve,
            name=args.name,
            affected_component=args.affected_component,
            impact_assessment=args.impact_assessment,
            pr_url=args.pr_url,
            pr_status=args.pr_status,
            discord_message_id=args.discord_message_id,
            project_id=args.project_id,
        )
    elif args.command == "batch":
        if args.file == "-":
            alerts = json.load(sys.stdin)
        else:
            alerts = json.load(open(args.file))
        record_batch(alerts, project_id=args.project_id)
    elif args.command == "list":
        list_alerts(limit=args.limit, project_id=args.project_id, all_projects=args.all)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
