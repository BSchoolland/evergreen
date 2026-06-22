#!/usr/bin/env python3
"""Manually trigger a skill run, recorded in history exactly like a cron run.

Enqueues the skill into skill_queue with source='manual'; the running watchdog
(evergreen-server.py) drains it through the same run_skill() path as scheduled
runs, so it gets a runs-table row with cost/model/effort and shows up on the
timeline — tagged `manual:<skill>` so test runs are distinguishable from cron.

Usage:
  trigger-skill.py <skill> [--no-force]   # e.g. trigger-skill.py check-bugs
  trigger-skill.py --list                 # list triggerable skills

By default manual runs --force past preconditions (so you can test a skill even
when it has no pending work). Pass --no-force to honor the precondition.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from evergreen.db import epoch, get_connection

SKILLS_DIR = REPO / ".claude" / "skills"
PID_PATH = Path.home() / ".evergreen" / "server.pid"
SUFFIX = "-evergreen"


def available_skills() -> list[str]:
    if not SKILLS_DIR.exists():
        return []
    return sorted(
        d.name[: -len(SUFFIX)]
        for d in SKILLS_DIR.iterdir()
        if d.is_dir() and d.name.endswith(SUFFIX)
    )


def watchdog_running() -> bool:
    try:
        pid = int(PID_PATH.read_text().strip())
    except (FileNotFoundError, ValueError):
        return False
    try:
        import os
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def main():
    args = [a for a in sys.argv[1:]]
    if not args or "--list" in args or "-l" in args:
        print("Triggerable skills:")
        for s in available_skills():
            print(f"  {s}")
        print("\nUsage: trigger-skill.py <skill> [--no-force]")
        return

    force = "--no-force" not in args
    positional = [a for a in args if not a.startswith("-")]
    if not positional:
        print("Error: no skill name given. Use --list to see options.", file=sys.stderr)
        sys.exit(1)

    skill = positional[0]
    if skill.endswith(SUFFIX):  # accept either "check-bugs" or "check-bugs-evergreen"
        skill = skill[: -len(SUFFIX)]

    skills = available_skills()
    if skill not in skills:
        print(f"Error: unknown skill '{skill}'.", file=sys.stderr)
        print("Available: " + ", ".join(skills), file=sys.stderr)
        sys.exit(1)

    conn = get_connection()
    conn.execute(
        "INSERT INTO skill_queue (skill, source, force) VALUES (?, 'manual', ?)",
        (skill, 1 if force else 0),
    )
    conn.commit()
    pending = conn.execute(
        "SELECT COUNT(*) FROM skill_queue WHERE status IN ('pending', 'running')"
    ).fetchone()[0]
    conn.close()

    print(f"Queued manual run of '{skill}' (force={force}).")
    if pending > 1:
        print(f"  {pending - 1} other item(s) ahead of it in the queue.")
    if watchdog_running():
        print("  Watchdog is running — it will start within ~60s. Watch the timeline.")
    else:
        print("  WARNING: watchdog is NOT running, so this won't execute.", file=sys.stderr)
        print("           Start it with: scripts/server.sh start", file=sys.stderr)


if __name__ == "__main__":
    main()
