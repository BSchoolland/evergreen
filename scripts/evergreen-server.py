#!/usr/bin/env python3
"""Evergreen cron server — runs scheduled skills via `claude -p`."""

import json
import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from evergreen.db import epoch, get_cli, get_connection, init_db

LOG_PATH = Path.home() / ".evergreen" / "server.log"
PID_PATH = Path.home() / ".evergreen" / "server.pid"

SKILLS = ["check-bugs", "hackernews-monitor", "tacos-audit", "update-status"]

DEFAULTS = {
    "check-bugs": 60,
    "hackernews-monitor": 360,
    "tacos-audit": 1440,
    "update-status": 240,
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("evergreen-server")


def ensure_cron_jobs():
    conn = get_connection()
    for skill in SKILLS:
        row = conn.execute(
            "SELECT 1 FROM cron_jobs WHERE skill = ?", (skill,)
        ).fetchone()
        if not row:
            conn.execute(
                "INSERT INTO cron_jobs (skill, interval_minutes) VALUES (?, ?)",
                (skill, DEFAULTS[skill]),
            )
    conn.commit()
    conn.close()


def has_new_items() -> bool:
    conn = get_connection()
    bugs = conn.execute(
        "SELECT COUNT(*) FROM bugs WHERE status = 'new'"
    ).fetchone()[0]
    alerts = conn.execute(
        "SELECT COUNT(*) FROM security_alerts WHERE status = 'new'"
    ).fetchone()[0]
    conn.close()
    return (bugs + alerts) > 0


def has_in_progress_items() -> bool:
    conn = get_connection()
    bugs = conn.execute(
        "SELECT COUNT(*) FROM bugs WHERE status = 'in_progress'"
    ).fetchone()[0]
    alerts = conn.execute(
        "SELECT COUNT(*) FROM security_alerts WHERE status = 'in_progress'"
    ).fetchone()[0]
    conn.close()
    return (bugs + alerts) > 0


def extract_session_id(json_output: str) -> str | None:
    try:
        data = json.loads(json_output)
        return data.get("session_id")
    except (json.JSONDecodeError, TypeError):
        return None


def run_summary(session_id: str, run_id: int):
    log.info("Resuming session %s for work summary", session_id)
    cli = get_cli()
    if cli != "claude":
        log.info("Summary skill only supported with claude CLI, skipping")
        return

    env = {**os.environ, "EVERGREEN_RUN_ID": str(run_id)}
    try:
        subprocess.run(
            ["claude", "-p", "/summary-of-work-evergreen",
             "--resume", session_id, "--dangerously-skip-permissions"],
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
        )
        log.info("Work summary recorded for run %d", run_id)
    except subprocess.TimeoutExpired:
        log.warning("Summary skill timed out for run %d", run_id)
    except Exception as e:
        log.error("Summary skill failed for run %d: %s", run_id, e)


def enqueue(skill: str):
    conn = get_connection()
    already = conn.execute(
        "SELECT 1 FROM skill_queue WHERE skill = ? AND status IN ('pending', 'running')",
        (skill,),
    ).fetchone()
    if already:
        log.info("Skill %s already queued or running, skipping", skill)
        conn.close()
        return
    conn.execute("INSERT INTO skill_queue (skill) VALUES (?)", (skill,))
    conn.commit()
    conn.close()
    log.info("Enqueued skill: %s", skill)


def drain_queue():
    conn = get_connection()
    running = conn.execute(
        "SELECT 1 FROM skill_queue WHERE status = 'running'"
    ).fetchone()
    if running:
        conn.close()
        return

    row = conn.execute(
        "SELECT id, skill FROM skill_queue WHERE status = 'pending' ORDER BY queued_at LIMIT 1"
    ).fetchone()
    if not row:
        conn.close()
        return

    queue_id, skill = row

    if skill == "update-status" and not has_in_progress_items():
        log.info("Update-status dequeued but no in-progress items, skipping")
        conn.execute(
            "UPDATE skill_queue SET status = 'done' WHERE id = ?",
            (queue_id,),
        )
        conn.commit()
        conn.close()
        return

    if skill == "triage" and not has_new_items():
        log.info("Triage dequeued but no new items, skipping")
        conn.execute(
            "UPDATE skill_queue SET status = 'done' WHERE id = ?",
            (queue_id,),
        )
        conn.commit()
        conn.close()
        return

    conn.execute(
        "UPDATE skill_queue SET status = 'running', started_at = ? WHERE id = ?",
        (epoch(), queue_id),
    )
    conn.commit()
    conn.close()

    run_skill(skill, queue_id)


def run_skill(skill: str, queue_id: int):
    log.info("Starting skill: %s", skill)
    conn = get_connection()
    conn.execute(
        "UPDATE cron_jobs SET last_run_at = ? WHERE skill = ?",
        (epoch(), skill),
    )
    conn.commit()

    run_id = conn.execute(
        "INSERT INTO runs (type, summary) VALUES (?, ?) RETURNING id",
        (f"cron:{skill}", f"Running /{skill}-evergreen"),
    ).fetchone()[0]
    conn.commit()
    conn.close()

    started = time.monotonic()
    cli = get_cli()
    session_id = None
    if cli == "claude":
        cmd = ["claude", "-p", f"/{skill}-evergreen",
               "--dangerously-skip-permissions", "--output-format", "json"]
    else:
        cmd = ["codex", "--ask-for-approval", "never", "--sandbox", "danger-full-access",
               "exec", "--skip-git-repo-check", f"/{skill}-evergreen"]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=900,
        )
        elapsed = time.monotonic() - started
        summary = f"exit={result.returncode} elapsed={elapsed:.0f}s"
        if result.returncode != 0 and result.stderr:
            summary += f" stderr={result.stderr[:200]}"
        if cli == "claude":
            session_id = extract_session_id(result.stdout)
        log.info("Finished skill %s: %s", skill, summary)
    except subprocess.TimeoutExpired:
        elapsed = time.monotonic() - started
        summary = f"TIMEOUT after {elapsed:.0f}s"
        log.warning("Skill %s timed out", skill)
    except Exception as e:
        elapsed = time.monotonic() - started
        summary = f"ERROR: {e}"
        log.error("Skill %s failed: %s", skill, e)

    conn = get_connection()
    conn.execute(
        "UPDATE runs SET finished_at = ?, summary = ? WHERE id = ?",
        (epoch(), summary, run_id),
    )
    conn.execute(
        "UPDATE skill_queue SET status = 'done' WHERE id = ?",
        (queue_id,),
    )
    conn.commit()
    conn.close()

    if session_id:
        run_summary(session_id, run_id)


def enqueue_triage():
    enqueue("triage")


def is_due(skill: str) -> bool:
    conn = get_connection()
    row = conn.execute(
        "SELECT interval_minutes, enabled, last_run_at FROM cron_jobs WHERE skill = ?",
        (skill,),
    ).fetchone()
    conn.close()

    if not row:
        return False
    interval_minutes, enabled, last_run_at = row
    if not enabled:
        return False
    if not last_run_at:
        return True

    return epoch() - last_run_at >= interval_minutes * 60


def tick():
    enqueued_any = False
    for skill in SKILLS:
        if is_due(skill):
            enqueue(skill)
            enqueued_any = True

    if enqueued_any:
        enqueue_triage()

    drain_queue()


def shutdown(signum, frame):
    log.info("Received signal %s, shutting down", signum)
    PID_PATH.unlink(missing_ok=True)
    sys.exit(0)


def main():
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    PID_PATH.write_text(str(os.getpid()) + "\n")
    log.info("Evergreen server started (pid %d)", os.getpid())

    init_db()
    ensure_cron_jobs()

    conn = get_connection()
    stale = conn.execute(
        "UPDATE skill_queue SET status = 'done' WHERE status = 'running' RETURNING skill"
    ).fetchall()
    if stale:
        log.warning("Cleared stale running entries from previous crash: %s",
                     [r[0] for r in stale])
    conn.commit()
    conn.close()

    while True:
        try:
            tick()
        except Exception:
            log.exception("Error in tick loop")
        time.sleep(60)


if __name__ == "__main__":
    main()
