#!/usr/bin/env python3
"""Record a security alert to the Evergreen database. Designed to be called by Claude Code skills."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from evergreen.db import get_connection


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
):
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO security_alerts
               (source, source_url, article_url, cve, name, severity, affected_component, summary, impact_assessment, pr_url, pr_status, discord_message_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (source, source_url, article_url, cve, name, severity, affected_component, summary, impact_assessment, pr_url, pr_status, discord_message_id),
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


def record_batch(alerts):
    """Insert multiple alerts from a list of dicts. Returns (inserted, skipped) counts."""
    conn = get_connection()
    inserted, skipped = 0, 0
    try:
        for a in alerts:
            try:
                conn.execute(
                    """INSERT INTO security_alerts
                       (source, source_url, article_url, cve, name, severity, affected_component, summary, impact_assessment, pr_url, pr_status, discord_message_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
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


def list_alerts(limit=20):
    conn = get_connection()
    rows = conn.execute(
        """SELECT id, created_at, source, cve, name, severity, affected_component, summary, status
           FROM security_alerts ORDER BY created_at DESC LIMIT ?""",
        (limit,),
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

    batch = sub.add_parser("batch", help="Add alerts from JSON file or stdin")
    batch.add_argument("file", nargs="?", default="-")

    sub.add_parser("list", help="List recent alerts")

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
        )
    elif args.command == "batch":
        if args.file == "-":
            alerts = json.load(sys.stdin)
        else:
            alerts = json.load(open(args.file))
        record_batch(alerts)
    elif args.command == "list":
        list_alerts()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
