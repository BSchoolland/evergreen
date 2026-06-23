#!/usr/bin/env python3
"""Record a bug to the Evergreen database. Designed to be called by Claude Code skills."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from evergreen.db import current_project_id, epoch, get_connection


def resolve_project_id(explicit=None):
    return explicit if explicit is not None else (current_project_id() or 1)


def record_bug(
    environment,
    error_pattern,
    severity,
    summary,
    source_query=None,
    occurrence_count=1,
    probable_root_cause=None,
    project_id=None,
):
    now = epoch()
    project_id = resolve_project_id(project_id)
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO bugs
               (project_id, environment, error_pattern, source_query, occurrence_count,
                first_seen_at, last_seen_at, severity, summary, probable_root_cause)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (project_id, environment, error_pattern, source_query, occurrence_count,
             now, now, severity, summary, probable_root_cause),
        )
        conn.commit()
        row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        print(f"Recorded bug #{row_id}: {summary}")
        return row_id
    finally:
        conn.close()


def update_bug(bug_id, **kwargs):
    conn = get_connection()
    try:
        sets = []
        vals = []
        for key, val in kwargs.items():
            if val is not None:
                sets.append(f"{key} = ?")
                vals.append(val)
        if not sets:
            print("Nothing to update.")
            return
        vals.append(bug_id)
        conn.execute(f"UPDATE bugs SET {', '.join(sets)} WHERE id = ?", vals)
        conn.commit()
        print(f"Updated bug #{bug_id}")
    finally:
        conn.close()


def resolve_bug(bug_id):
    now = epoch()
    update_bug(bug_id, status="resolved", resolved_at=now)


def bump_bug(bug_id, count=1):
    """Increment occurrence count and update last_seen_at."""
    now = epoch()
    conn = get_connection()
    try:
        conn.execute(
            """UPDATE bugs SET occurrence_count = occurrence_count + ?,
               last_seen_at = ? WHERE id = ?""",
            (count, now, bug_id),
        )
        conn.commit()
        print(f"Bumped bug #{bug_id} by {count}")
    finally:
        conn.close()


def list_bugs(open_only=False, dismissed_only=False, limit=20, project_id=None, all_projects=False):
    conn = get_connection()
    clauses = []
    params = []
    if dismissed_only:
        clauses.append("status = 'dismissed'")
    elif open_only:
        clauses.append("status NOT IN ('resolved', 'dismissed')")
    if not all_projects:
        clauses.append("project_id = ?")
        params.append(resolve_project_id(project_id))
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)
    rows = conn.execute(
        f"""SELECT id, created_at, environment, severity, summary,
                   error_pattern, occurrence_count, status, last_seen_at
            FROM bugs {where} ORDER BY created_at DESC LIMIT ?""",
        params,
    ).fetchall()
    conn.close()
    if not rows:
        print("No bugs recorded." if not open_only else "No open bugs.")
        return
    for row in rows:
        id, ts, env, sev, summary, pattern, count, status, last_seen = row
        print(f"#{id} [{env}] [{sev}] {summary} (x{count}, last: {last_seen}) — {status}")


def main():
    parser = argparse.ArgumentParser(description="Record bugs to Evergreen DB")
    sub = parser.add_subparsers(dest="command")

    add = sub.add_parser("add", help="Add a single bug")
    add.add_argument("--environment", required=True, choices=["staging", "prod"])
    add.add_argument("--error-pattern", required=True)
    add.add_argument("--severity", required=True, choices=["critical", "high", "medium", "low"])
    add.add_argument("--summary", required=True)
    add.add_argument("--source-query")
    add.add_argument("--occurrence-count", type=int, default=1)
    add.add_argument("--probable-root-cause")
    add.add_argument("--project-id", type=int)

    up = sub.add_parser("update", help="Update a bug")
    up.add_argument("id", type=int)
    up.add_argument("--status", choices=["new", "verified", "unverified", "blocked", "not_actionable", "in_progress", "backlog", "action_needed", "resolved", "dismissed"])
    up.add_argument("--pr-url")
    up.add_argument("--probable-root-cause")
    up.add_argument("--severity", choices=["critical", "high", "medium", "low"])
    up.add_argument("--disposition-reason", help="Why the issue was dismissed or moved to backlog")
    up.add_argument("--dismissed-by", choices=["verify", "pr_closed", "owner"])

    res = sub.add_parser("resolve", help="Resolve a bug")
    res.add_argument("id", type=int)

    b = sub.add_parser("bump", help="Increment occurrence count")
    b.add_argument("id", type=int)
    b.add_argument("--count", type=int, default=1)

    ls = sub.add_parser("list", help="List bugs")
    ls.add_argument("--open", action="store_true", help="Only show open bugs")
    ls.add_argument("--dismissed", action="store_true", help="Only show dismissed bugs")
    ls.add_argument("--limit", type=int, default=20)
    ls.add_argument("--project-id", type=int)
    ls.add_argument("--all", action="store_true", help="Show bugs across all projects")

    args = parser.parse_args()

    if args.command == "add":
        record_bug(
            environment=args.environment,
            error_pattern=args.error_pattern,
            severity=args.severity,
            summary=args.summary,
            source_query=args.source_query,
            occurrence_count=args.occurrence_count,
            probable_root_cause=args.probable_root_cause,
            project_id=args.project_id,
        )
    elif args.command == "update":
        update_bug(
            args.id,
            status=args.status,
            pr_url=args.pr_url,
            probable_root_cause=args.probable_root_cause,
            severity=args.severity,
            disposition_reason=args.disposition_reason,
            dismissed_by=args.dismissed_by,
        )
    elif args.command == "resolve":
        resolve_bug(args.id)
    elif args.command == "bump":
        bump_bug(args.id, args.count)
    elif args.command == "list":
        list_bugs(open_only=args.open, dismissed_only=args.dismissed, limit=args.limit,
                  project_id=args.project_id, all_projects=args.all)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
