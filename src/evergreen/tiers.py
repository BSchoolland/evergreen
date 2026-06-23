"""Project types, depth tiers, and the agent-plane schedules they imply.

Two planes:
  * MONITORING plane (uptime/SSL) is deterministic code that runs for EVERY active
    project with a base_url, regardless of type/tier. It is ~free and lives in
    uptime.py — it is not scheduled through cron_jobs.
  * AGENT plane (judgment skills) costs money. Which skills a project runs, and how
    often, is what `type` and `depth_tier` decide. Those become cron_jobs rows.

cron_jobs is the live source of truth once seeded — tune intervals per project on
the dashboard. The tier just provides the starting point.
"""

from evergreen.db import epoch, get_connection

# Agent-plane skills per project type, with BASE intervals in minutes (the
# "standard" tier). A type with no agent skills is monitoring-plane only.
#   code_db    : deep log/PR monitoring (TACOS-style)
#   wordpress  : alert-only today — monitoring plane only (wp-cli/wpscan later)
#   static     : monitoring plane only
TYPE_SKILLS: dict[str, dict[str, int]] = {
    "code_db": {
        "check-bugs": 720,
        "hackernews-monitor": 1440,
        "tacos-audit": 4320,
        "update-status": 480,
        # Cross-bug analysis: unstick stranded issues + consolidate recurring
        # design flaws into themes. Infrequent.
        "reconcile": 4320,
    },
    "wordpress": {},
    "static": {},
}

# Depth tier scales the base intervals. Smaller multiplier = runs more often =
# deeper coverage = more spend. Tune freely.
TIER_MULTIPLIER: dict[str, float] = {
    "lite": 3.0,
    "standard": 1.0,
    "deep": 0.5,
}

PROJECT_TYPES = tuple(TYPE_SKILLS.keys())
DEPTH_TIERS = tuple(TIER_MULTIPLIER.keys())


def schedule_for(project_type: str, tier: str) -> dict[str, int]:
    """The {skill: interval_minutes} an agent-plane schedule should have for this
    (type, tier). Empty for monitoring-plane-only types."""
    base = TYPE_SKILLS.get(project_type, {})
    mult = TIER_MULTIPLIER.get(tier, 1.0)
    return {skill: max(1, round(interval * mult)) for skill, interval in base.items()}


def seed_project_cron(project_id: int, project_type: str, tier: str,
                      reset: bool = False) -> dict[str, int]:
    """Create cron_jobs rows for a project from its (type, tier). Idempotent: by
    default existing rows (and their hand-tuned intervals) are preserved; pass
    reset=True to overwrite intervals back to the tier defaults."""
    sched = schedule_for(project_type, tier)
    conn = get_connection()
    for skill, interval in sched.items():
        if reset:
            conn.execute(
                "INSERT INTO cron_jobs (project_id, skill, interval_minutes) VALUES (?, ?, ?) "
                "ON CONFLICT(project_id, skill) DO UPDATE SET interval_minutes = excluded.interval_minutes",
                (project_id, skill, interval),
            )
        else:
            conn.execute(
                "INSERT OR IGNORE INTO cron_jobs (project_id, skill, interval_minutes) VALUES (?, ?, ?)",
                (project_id, skill, interval),
            )
    conn.commit()
    conn.close()
    return sched
