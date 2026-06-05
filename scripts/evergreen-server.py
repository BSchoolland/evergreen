#!/usr/bin/env python3
"""Evergreen cron server — runs scheduled skills via the configured engine.

The engine is read from ~/.evergreen/config (get_cli): "pi" (default going
forward), "claude", or "codex". This is the *watchdog* dev: it runs in its own
clone of the target repo (the `project_path` config / EVERGREEN_PROJECT_PATH),
separate from the interactive dev's clone, so the two never collide on git state.
"""

import json
import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from evergreen.db import epoch, get_cli, get_config, get_connection, get_project_path, init_db

LOG_PATH = Path.home() / ".evergreen" / "server.log"
PID_PATH = Path.home() / ".evergreen" / "server.pid"
SYNC_SKILLS_SCRIPT = REPO / "scripts" / "sync-pi-skills.py"

# Detection skills run on a schedule. verify-bug and triage are triggered
# after detection, not independently scheduled.
SCHEDULED_SKILLS = ["check-bugs", "hackernews-monitor", "tacos-audit", "update-status"]

SCHEDULE_DEFAULTS = {
    "check-bugs": 60,
    "hackernews-monitor": 360,
    "tacos-audit": 1440,
    "update-status": 240,
}

MAX_VERIFY_RUNS = 5

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("evergreen-server")


# --- Preconditions: return True if the skill has work to do ---

def has_new_bugs() -> bool:
    conn = get_connection()
    count = conn.execute(
        "SELECT COUNT(*) FROM bugs WHERE status = 'new'"
    ).fetchone()[0]
    conn.close()
    return count > 0


def has_triageable_items() -> bool:
    conn = get_connection()
    verified_bugs = conn.execute(
        "SELECT COUNT(*) FROM bugs WHERE status IN ('verified', 'unverified')"
    ).fetchone()[0]
    new_alerts = conn.execute(
        "SELECT COUNT(*) FROM security_alerts WHERE status = 'new'"
    ).fetchone()[0]
    conn.close()
    return (verified_bugs + new_alerts) > 0


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


PRECONDITIONS = {
    "verify-bug": has_new_bugs,
    "triage": has_triageable_items,
    "update-status": has_in_progress_items,
}


# --- Queue management ---

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


def pop_next() -> tuple[int, str] | None:
    conn = get_connection()
    running = conn.execute(
        "SELECT 1 FROM skill_queue WHERE status = 'running'"
    ).fetchone()
    if running:
        conn.close()
        return None

    row = conn.execute(
        "SELECT id, skill FROM skill_queue WHERE status = 'pending' ORDER BY queued_at LIMIT 1"
    ).fetchone()
    conn.close()
    if not row:
        return None
    return row[0], row[1]


def skip(queue_id: int, skill: str):
    conn = get_connection()
    conn.execute("UPDATE skill_queue SET status = 'done' WHERE id = ?", (queue_id,))
    conn.commit()
    conn.close()
    log.info("%s skipped — precondition not met", skill)


def mark_running(queue_id: int):
    conn = get_connection()
    conn.execute(
        "UPDATE skill_queue SET status = 'running', started_at = ? WHERE id = ?",
        (epoch(), queue_id),
    )
    conn.commit()
    conn.close()


def mark_done(queue_id: int):
    conn = get_connection()
    conn.execute("UPDATE skill_queue SET status = 'done' WHERE id = ?", (queue_id,))
    conn.commit()
    conn.close()


def drain_queue():
    item = pop_next()
    if not item:
        return
    queue_id, skill = item

    check = PRECONDITIONS.get(skill)
    if check and not check():
        skip(queue_id, skill)
        return

    mark_running(queue_id)
    run_skill(skill, queue_id)
    mark_done(queue_id)

    if skill == "verify-bug":
        maybe_requeue_verify()


def maybe_requeue_verify():
    if not has_new_bugs():
        return
    conn = get_connection()
    recent = conn.execute(
        "SELECT COUNT(*) FROM skill_queue WHERE skill = 'verify-bug' AND status = 'done' AND started_at > ?",
        (epoch() - 3600,),
    ).fetchone()[0]
    conn.close()
    if recent < MAX_VERIFY_RUNS:
        log.info("Still new bugs after verify-bug, re-enqueuing (%d/%d)", recent + 1, MAX_VERIFY_RUNS)
        enqueue("verify-bug")
    else:
        log.info("Hit max verify-bug runs (%d) this cycle, moving on", MAX_VERIFY_RUNS)


# --- Skill execution ---

def sync_pi_skills():
    """Regenerate .pi/skills from .claude/skills so pi sees fresh evergreen skills."""
    if get_cli() != "pi" or not SYNC_SKILLS_SCRIPT.exists():
        return
    try:
        subprocess.run([sys.executable, str(SYNC_SKILLS_SCRIPT)],
                       capture_output=True, text=True, timeout=60, cwd=str(REPO))
        log.info("Synced pi skills")
    except Exception as e:
        log.warning("Failed to sync pi skills: %s", e)


def reset_project_branch():
    # The watchdog server has no EVERGREEN_PROJECT_PATH in its own env, so this
    # resolves to the `project_path` config — the watchdog's clone (clone A).
    project_path = get_project_path()
    if not project_path:
        return
    try:
        subprocess.run(
            ["git", "checkout", "master"],
            cwd=project_path, capture_output=True, timeout=30,
        )
        subprocess.run(
            ["git", "pull"],
            cwd=project_path, capture_output=True, timeout=60,
        )
        log.info("Reset project to master and pulled latest")
    except Exception as e:
        log.warning("Failed to reset project branch: %s", e)


def run_skill(skill: str, queue_id: int):
    log.info("Starting skill: %s", skill)
    reset_project_branch()

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
    session_dir = None
    run_cwd = None
    run_env = None
    if cli == "claude":
        cmd = ["claude", "-p", f"/{skill}-evergreen",
               "--dangerously-skip-permissions", "--output-format", "json"]
    elif cli == "pi":
        # Run from the evergreen repo so pi discovers .pi/skills; point the agent
        # at the watchdog's clone via EVERGREEN_PROJECT_PATH. A per-run session-dir
        # lets the summary step resume this exact session with --continue.
        session_dir = REPO / ".pi" / "sessions" / "watchdog" / f"run-{run_id}"
        cmd = ["pi", "-p", "--session-dir", str(session_dir)]
        model = get_config("pi_model")
        if model:
            cmd += ["--model", model]
        cmd += [f"/skill:{skill}-evergreen"]
        run_cwd = str(REPO)
        run_env = {**os.environ, "EVERGREEN_PROJECT_PATH": get_project_path() or ""}
    else:
        cmd = ["codex", "--ask-for-approval", "never", "--sandbox", "danger-full-access",
               "exec", "--skip-git-repo-check", f"/{skill}-evergreen"]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=3600,
            cwd=run_cwd,
            env=run_env,
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
    conn.commit()
    conn.close()

    if cli == "claude" and session_id:
        run_summary(run_id, session_id=session_id)
    elif cli == "pi" and session_dir:
        run_summary(run_id, session_dir=session_dir)

    deploy_lakebed()


def extract_session_id(json_output: str) -> str | None:
    try:
        data = json.loads(json_output)
        return data.get("session_id")
    except (json.JSONDecodeError, TypeError):
        return None


def run_summary(run_id: int, session_id: str = None, session_dir: Path = None):
    """Resume the just-finished agent session to record what it did this run."""
    cli = get_cli()
    env = {**os.environ, "EVERGREEN_RUN_ID": str(run_id)}
    try:
        if cli == "claude" and session_id:
            log.info("Resuming claude session %s for work summary", session_id)
            cmd = ["claude", "-p", "/summary-of-work-evergreen",
                   "--resume", session_id, "--dangerously-skip-permissions"]
            cwd = None
        elif cli == "pi" and session_dir:
            log.info("Resuming pi session in %s for work summary", session_dir)
            env["EVERGREEN_PROJECT_PATH"] = get_project_path() or ""
            cmd = ["pi", "-p", "--session-dir", str(session_dir), "--continue"]
            model = get_config("pi_model")
            if model:
                cmd += ["--model", model]
            cmd += ["/skill:summary-of-work-evergreen"]
            cwd = str(REPO)
        else:
            log.info("No resumable session for run %d, skipping summary", run_id)
            return
        subprocess.run(cmd, capture_output=True, text=True, timeout=120, env=env, cwd=cwd)
        log.info("Work summary recorded for run %d", run_id)
    except subprocess.TimeoutExpired:
        log.warning("Summary skill timed out for run %d", run_id)
    except Exception as e:
        log.error("Summary skill failed for run %d: %s", run_id, e)


def deploy_lakebed():
    script = Path(__file__).resolve().parent / "deploy-lakebed.py"
    if not script.exists():
        return
    try:
        subprocess.Popen(
            [sys.executable, str(script)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        log.info("Triggered Lakebed deploy")
    except Exception as e:
        log.warning("Lakebed deploy failed to start: %s", e)


# --- Scheduling ---

def ensure_cron_jobs():
    conn = get_connection()
    for skill in SCHEDULED_SKILLS:
        row = conn.execute(
            "SELECT 1 FROM cron_jobs WHERE skill = ?", (skill,)
        ).fetchone()
        if not row:
            conn.execute(
                "INSERT INTO cron_jobs (skill, interval_minutes) VALUES (?, ?)",
                (skill, SCHEDULE_DEFAULTS[skill]),
            )
    conn.commit()
    conn.close()


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
    for skill in SCHEDULED_SKILLS:
        if is_due(skill):
            enqueue(skill)
            enqueued_any = True

    if enqueued_any:
        enqueue("verify-bug")
        enqueue("triage")

    drain_queue()


# --- Main ---

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
    sync_pi_skills()

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
