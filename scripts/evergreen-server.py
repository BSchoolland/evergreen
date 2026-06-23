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

from evergreen import uptime
from evergreen.db import (
    EVERGREEN_DIR,
    compute_cost,
    epoch,
    get_cli,
    get_config,
    get_connection,
    list_projects,
    project_config_value,
    init_db,
)
from evergreen.pi_usage import session_metrics
from evergreen.tiers import seed_project_cron

LOG_PATH = EVERGREEN_DIR / "server.log"
PID_PATH = EVERGREEN_DIR / "server.pid"
SYNC_SKILLS_SCRIPT = REPO / "scripts" / "sync-pi-skills.py"

# verify-bug and triage are triggered per-project when there's work, not scheduled
# through cron_jobs. Which detection skills a project runs comes from its cron_jobs
# rows (seeded from its type + depth tier — see evergreen.tiers).
TRIGGERED_SKILLS = ["verify-bug", "triage"]

MAX_VERIFY_RUNS = 5

# Per-skill model + reasoning effort. Heavy skills (verify-bug, triage) keep the
# global default — pi's configured model (gpt-5.5) at its default thinking level
# (xhigh). Light/mechanical skills run on a cheaper model and/or lower effort.
# NOTE: the model MUST carry its provider prefix (e.g. "openai-codex/...") so it
# resolves to the intended provider and not, say, openrouter.
#
# Resolution order per skill (first non-empty wins):
#   1. config table:  model:<skill> / thinking:<skill>   (runtime override, no deploy)
#   2. SKILL_MODELS below
#   3. config table:  pi_model                            (global model override)
#   4. pi's own configured defaults                       (model=None, thinking=None)
SKILL_MODELS = {
    # "write 2 sentences about what you did" — ~7x cheaper at mini/minimal with no
    # measurable quality loss (see token-cost analysis 2026-06-16).
    "summary-of-work": {"model": "openai-codex/gpt-5.4-mini", "thinking": "minimal"},
    # Read/classify/reconcile work — cheap model is fine, mistakes are visible and
    # self-correct on the next run.
    "update-status": {"model": "openai-codex/gpt-5.4-mini", "thinking": "low"},
    "tacos-audit": {"model": "openai-codex/gpt-5.4-mini", "thinking": "medium"},
    "hackernews-monitor": {"model": "openai-codex/gpt-5.4-mini", "thinking": "medium"},
    # Detector — mini/medium: ~$0.54/run (half of gpt-5.5/medium), finds real bugs.
    # It never notifies (detection-only); pings are gated behind verify-bug, so
    # mini's looser notification judgment can't reach the owner. n=2 trials 2026-06-16.
    "check-bugs": {"model": "openai-codex/gpt-5.4-mini", "thinking": "medium"},
    # Left at the global default (gpt-5.5 / xhigh) — high-stakes reasoning:
    #   verify-bug  : scientific root-cause + the ONLY skill that notifies the owner
    #   triage      : writes code + opens PRs
}


def resolve_model(skill: str):
    """Return (model, thinking) for a skill. Either may be None to inherit pi's
    own configured default. See SKILL_MODELS for the resolution order."""
    cfg = SKILL_MODELS.get(skill, {})
    model = (
        get_config(f"model:{skill}")
        or cfg.get("model")
        or get_config("pi_model")
    )
    thinking = get_config(f"thinking:{skill}") or cfg.get("thinking")
    return model, thinking


# Per-skill model + effort for the *claude* engine — the cost tiering above,
# translated to Claude. Heavy skills (verify-bug, triage) fall through to the
# claude default (Opus / high); light/mechanical skills run on Sonnet at the same
# effort the pi mapping used, and the work summary runs on Haiku.
# Models use bare aliases ("opus"/"sonnet"/"haiku") so each always resolves to the
# latest of that tier — new model drops are picked up with no code change.
# Mapping requested 2026-06-22: gpt-5.5/xhigh -> opus/high; gpt-5.4-mini -> sonnet
# (same effort); summary-of-work -> haiku/low (claude has no "minimal").
CLAUDE_DEFAULT_MODEL = "opus"
CLAUDE_DEFAULT_EFFORT = "high"
CLAUDE_SKILL_MODELS = {
    "summary-of-work": {"model": "haiku", "effort": "low"},
    "update-status": {"model": "sonnet", "effort": "low"},
    "tacos-audit": {"model": "sonnet", "effort": "medium"},
    "hackernews-monitor": {"model": "sonnet", "effort": "medium"},
    "check-bugs": {"model": "sonnet", "effort": "medium"},
    # verify-bug, triage: inherit the claude default (opus / high).
}


def resolve_model_claude(skill: str):
    """Return (model, effort) for a skill on the claude engine. Resolution order
    (first non-empty wins):
      1. config table:  claude_model:<skill> / claude_effort:<skill>
      2. CLAUDE_SKILL_MODELS
      3. config table:  claude_model / claude_effort   (global overrides)
      4. CLAUDE_DEFAULT_MODEL / CLAUDE_DEFAULT_EFFORT
    """
    cfg = CLAUDE_SKILL_MODELS.get(skill, {})
    model = (
        get_config(f"claude_model:{skill}")
        or cfg.get("model")
        or get_config("claude_model")
        or CLAUDE_DEFAULT_MODEL
    )
    effort = (
        get_config(f"claude_effort:{skill}")
        or cfg.get("effort")
        or get_config("claude_effort")
        or CLAUDE_DEFAULT_EFFORT
    )
    return model, effort

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("evergreen-server")


# --- Preconditions: return True if the skill has work to do for this project ---

def has_new_bugs(project_id: int) -> bool:
    conn = get_connection()
    count = conn.execute(
        "SELECT COUNT(*) FROM bugs WHERE status = 'new' AND project_id = ?", (project_id,)
    ).fetchone()[0]
    conn.close()
    return count > 0


def has_triageable_items(project_id: int) -> bool:
    conn = get_connection()
    verified_bugs = conn.execute(
        "SELECT COUNT(*) FROM bugs WHERE status IN ('verified', 'unverified') AND project_id = ?",
        (project_id,),
    ).fetchone()[0]
    new_alerts = conn.execute(
        "SELECT COUNT(*) FROM security_alerts WHERE status = 'new' AND project_id = ?",
        (project_id,),
    ).fetchone()[0]
    conn.close()
    return (verified_bugs + new_alerts) > 0


def has_in_progress_items(project_id: int) -> bool:
    conn = get_connection()
    bugs = conn.execute(
        "SELECT COUNT(*) FROM bugs WHERE status = 'in_progress' AND project_id = ?", (project_id,)
    ).fetchone()[0]
    alerts = conn.execute(
        "SELECT COUNT(*) FROM security_alerts WHERE status = 'in_progress' AND project_id = ?",
        (project_id,),
    ).fetchone()[0]
    conn.close()
    return (bugs + alerts) > 0


PRECONDITIONS = {
    "verify-bug": has_new_bugs,
    "triage": has_triageable_items,
    "update-status": has_in_progress_items,
}


# --- Queue management ---

def enqueue(project_id: int, skill: str, source: str = "cron", force: bool = False):
    conn = get_connection()
    # Cron enqueues are de-duped per (project, skill) — the scheduler may re-fire
    # while one is pending. Manual triggers are always inserted.
    if source == "cron":
        already = conn.execute(
            "SELECT 1 FROM skill_queue WHERE project_id = ? AND skill = ? AND status IN ('pending', 'running')",
            (project_id, skill),
        ).fetchone()
        if already:
            conn.close()
            return
    conn.execute(
        "INSERT INTO skill_queue (project_id, skill, source, force) VALUES (?, ?, ?, ?)",
        (project_id, skill, source, 1 if force else 0),
    )
    conn.commit()
    conn.close()
    log.info("Enqueued skill: p%d/%s (source=%s, force=%s)", project_id, skill, source, force)


def pop_next() -> tuple[int, int, str, str, int] | None:
    conn = get_connection()
    # One agent run at a time across ALL projects (the chosen concurrency model).
    running = conn.execute(
        "SELECT 1 FROM skill_queue WHERE status = 'running'"
    ).fetchone()
    if running:
        conn.close()
        return None

    row = conn.execute(
        "SELECT id, project_id, skill, source, force FROM skill_queue WHERE status = 'pending' ORDER BY queued_at LIMIT 1"
    ).fetchone()
    conn.close()
    if not row:
        return None
    return row[0], row[1], row[2], row[3], row[4]


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
    queue_id, project_id, skill, source, force = item

    # Manual triggers can force-run regardless of preconditions (for testing a
    # skill even when it has no pending work). Cron runs always honor them.
    check = PRECONDITIONS.get(skill)
    if check and not force and not check(project_id):
        skip(queue_id, skill)
        return

    mark_running(queue_id)
    # Manual runs are tagged manual:<skill> so they're distinguishable on the
    # timeline, and they don't disturb the cron schedule (last_run_at).
    run_skill(project_id, skill, queue_id,
              run_type=f"{source}:{skill}",
              update_schedule=(source == "cron"))
    mark_done(queue_id)

    if source == "cron" and skill == "verify-bug":
        maybe_requeue_verify(project_id)


def maybe_requeue_verify(project_id: int):
    if not has_new_bugs(project_id):
        return
    conn = get_connection()
    recent = conn.execute(
        "SELECT COUNT(*) FROM skill_queue WHERE project_id = ? AND skill = 'verify-bug' "
        "AND status = 'done' AND started_at > ?",
        (project_id, epoch() - 3600),
    ).fetchone()[0]
    conn.close()
    if recent < MAX_VERIFY_RUNS:
        log.info("Still new bugs in p%d after verify-bug, re-enqueuing (%d/%d)",
                 project_id, recent + 1, MAX_VERIFY_RUNS)
        enqueue(project_id, "verify-bug")
    else:
        log.info("Hit max verify-bug runs (%d) for p%d this cycle, moving on",
                 MAX_VERIFY_RUNS, project_id)


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


def reset_project_branch(project_path: str | None):
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


def _cron_model_override(project_id: int, skill: str) -> tuple[str | None, str | None]:
    """Per-(project, skill) model/effort override stored on cron_jobs, if any."""
    conn = get_connection()
    row = conn.execute(
        "SELECT model, effort FROM cron_jobs WHERE project_id = ? AND skill = ?",
        (project_id, skill),
    ).fetchone()
    conn.close()
    return (row[0], row[1]) if row else (None, None)


def run_skill(project_id: int, skill: str, queue_id: int, run_type: str = None,
              update_schedule: bool = True):
    run_type = run_type or f"cron:{skill}"
    log.info("Starting skill: p%d/%s (%s)", project_id, skill, run_type)
    project_path = project_config_value(project_id, "project_path")
    reset_project_branch(project_path)

    conn = get_connection()
    if update_schedule:
        conn.execute(
            "UPDATE cron_jobs SET last_run_at = ? WHERE project_id = ? AND skill = ?",
            (epoch(), project_id, skill),
        )
        conn.commit()
    run_id = conn.execute(
        "INSERT INTO runs (project_id, type, summary) VALUES (?, ?, ?) RETURNING id",
        (project_id, run_type, f"Running /{skill}-evergreen"),
    ).fetchone()[0]
    conn.commit()
    conn.close()

    over_model, over_effort = _cron_model_override(project_id, skill)

    started = time.monotonic()
    cli = get_cli()
    session_id = None
    session_dir = None
    claude_stdout = None
    claude_model = None
    claude_effort = None
    # Always run the agent from the repo so it discovers the repo-local skills
    # (.claude/skills for claude/codex, .pi/skills for pi). Otherwise a watchdog
    # launched from $HOME (reboot/cron/ssh) resolves no slash command and every
    # run dies instantly with "Unknown command: /<skill>-evergreen".
    run_cwd = str(REPO)
    run_env = {**os.environ, "EVERGREEN_RUN_ID": str(run_id),
               "EVERGREEN_PROJECT_ID": str(project_id),
               "EVERGREEN_PROJECT_PATH": project_path or ""}
    if cli == "claude":
        claude_model, claude_effort = resolve_model_claude(skill)
        claude_model = over_model or claude_model
        claude_effort = over_effort or claude_effort
        cmd = ["claude", "-p", f"/{skill}-evergreen",
               "--dangerously-skip-permissions", "--output-format", "json",
               "--model", claude_model, "--effort", claude_effort]
    elif cli == "pi":
        # Point the agent at the watchdog's clone via EVERGREEN_PROJECT_PATH. A
        # per-run session-dir lets the summary step resume this exact session
        # with --continue. (cwd is the repo — set above for every engine.)
        session_dir = REPO / ".pi" / "sessions" / "watchdog" / f"run-{run_id}"
        cmd = ["pi", "-p", "--session-dir", str(session_dir)]
        model, thinking = resolve_model(skill)
        model = over_model or model
        thinking = over_effort or thinking
        if model:
            cmd += ["--model", model]
        if thinking:
            cmd += ["--thinking", thinking]
        cmd += [f"/skill:{skill}-evergreen"]
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
            claude_stdout = result.stdout
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
        run_summary(run_id, project_path, session_id=session_id)
        record_run_cost_claude(run_id, claude_stdout, model=claude_model, effort=claude_effort)
    elif cli == "pi" and session_dir:
        run_summary(run_id, project_path, session_dir=session_dir)
        # Record cost AFTER the summary step so the run total includes it.
        record_run_cost(run_id, session_dir)

    deploy_lakebed()



def _store_run_cost(run_id, model, effort, tokens, input_tokens, output_tokens, reported_cost):
    """Persist a run's cost. Cost is NOTIONAL — computed from the model_pricing
    table (compute_cost), so subscription/free models still carry budget weight.
    Falls back to the provider-reported cost only when the model isn't priced."""
    notional = compute_cost(model, input_tokens, output_tokens, tokens)
    cost = notional if notional is not None else reported_cost
    conn = get_connection()
    conn.execute(
        "UPDATE runs SET cost = ?, tokens = ?, input_tokens = ?, output_tokens = ?, model = ?, effort = ? WHERE id = ?",
        (cost, tokens, input_tokens, output_tokens, model, effort, run_id),
    )
    conn.commit()
    conn.close()
    log.info("Run %d: $%.4f (notional=%s), %s tokens, %s/%s",
             run_id, cost or 0, notional is not None, tokens or 0, model, effort)


def record_run_cost(run_id: int, session_dir: Path):
    try:
        cost, tokens, model, effort = session_metrics(session_dir)
    except Exception as e:
        log.warning("Could not compute metrics for run %d: %s", run_id, e)
        return
    if cost is None and model is None:
        return
    _store_run_cost(run_id, model, effort, tokens, None, None, cost)


def record_run_cost_claude(run_id: int, json_output: str, model: str = None, effort: str = None):
    """Record cost/tokens/model/effort from `claude -p --output-format json` so cost
    tracking works on the claude engine, not just pi. The resolved model/effort
    (what we asked for) take precedence over the JSON."""
    if not json_output:
        return
    try:
        data = json.loads(json_output)
    except (json.JSONDecodeError, TypeError):
        return
    reported_cost = data.get("total_cost_usd")
    usage = data.get("usage") or {}
    input_tokens = output_tokens = tokens = None
    if isinstance(usage, dict):
        # Fold cache tokens into the priced input: cache creation ~= input rate,
        # cache reads ~10x cheaper, so weight reads at 0.1. Without this, a
        # cache-heavy agent's notional cost reads far below reality.
        base_in = usage.get("input_tokens") or 0
        cache_create = usage.get("cache_creation_input_tokens") or 0
        cache_read = usage.get("cache_read_input_tokens") or 0
        input_tokens = base_in + cache_create + int(cache_read * 0.1)
        output_tokens = usage.get("output_tokens")
        tokens = sum(v for v in usage.values() if isinstance(v, int)) or None
    model = model or data.get("model")
    if reported_cost is None and tokens is None and model is None:
        return
    _store_run_cost(run_id, model, effort, tokens, input_tokens, output_tokens, reported_cost)


def extract_session_id(json_output: str) -> str | None:
    try:
        data = json.loads(json_output)
        return data.get("session_id")
    except (json.JSONDecodeError, TypeError):
        return None


def run_summary(run_id: int, project_path: str | None, session_id: str = None, session_dir: Path = None):
    """Resume the just-finished agent session to record what it did this run."""
    cli = get_cli()
    env = {**os.environ, "EVERGREEN_RUN_ID": str(run_id)}
    try:
        if cli == "claude" and session_id:
            log.info("Resuming claude session %s for work summary", session_id)
            sm_model, sm_effort = resolve_model_claude("summary-of-work")
            cmd = ["claude", "-p", "/summary-of-work-evergreen",
                   "--resume", session_id, "--dangerously-skip-permissions",
                   "--model", sm_model, "--effort", sm_effort]
            cwd = None
        elif cli == "pi" and session_dir:
            log.info("Resuming pi session in %s for work summary", session_dir)
            env["EVERGREEN_PROJECT_PATH"] = project_path or ""
            cmd = ["pi", "-p", "--session-dir", str(session_dir), "--continue"]
            model, thinking = resolve_model("summary-of-work")
            if model:
                cmd += ["--model", model]
            if thinking:
                cmd += ["--thinking", thinking]
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
        # Route output to the server log (not DEVNULL) so a failing deploy — e.g.
        # the artifact exceeding Lakebed's request-size limit — is visible instead
        # of silently swallowed.
        logfh = open(LOG_PATH, "a")
        subprocess.Popen(
            [sys.executable, str(script)],
            stdout=logfh,
            stderr=subprocess.STDOUT,
        )
        log.info("Triggered Lakebed deploy")
    except Exception as e:
        log.warning("Lakebed deploy failed to start: %s", e)


# --- Scheduling ---

def ensure_cron_jobs():
    """Make sure every active project has its type+tier agent-plane schedule seeded.
    Idempotent: existing (possibly hand-tuned) rows are preserved."""
    for p in list_projects(include_inactive=False):
        seed_project_cron(p["id"], p["type"], p["depth_tier"])


def due_cron_skills(project_id: int) -> list[str]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT skill, interval_minutes, enabled, last_run_at FROM cron_jobs WHERE project_id = ?",
        (project_id,),
    ).fetchall()
    conn.close()
    now = epoch()
    due = []
    for skill, interval, enabled, last_run_at in rows:
        if not enabled:
            continue
        if not last_run_at or now - last_run_at >= interval * 60:
            due.append(skill)
    return due


def tick():
    # Monitoring plane first — cheap, deterministic, every active project. Self-
    # throttles per project, so it's safe to call every tick.
    try:
        uptime.run_sweep(log)
    except Exception:
        log.exception("uptime sweep failed")

    # Agent plane — per (project, skill), then per-project triggered skills.
    for p in list_projects(include_inactive=False):
        pid = p["id"]
        for skill in due_cron_skills(pid):
            enqueue(pid, skill)
        if has_new_bugs(pid):
            enqueue(pid, "verify-bug")
        if has_triageable_items(pid):
            enqueue(pid, "triage")

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
