#!/usr/bin/env python3
"""Manage evergreen projects — the CLI behind /onboard-project-evergreen.

Examples:
  project.py list
  project.py add --name "My Site" --type static --base-url https://my.site/ --tier standard
  project.py add --name "Shop" --type wordpress --base-url https://shop.com/ \
      --ssh wp-server --wp-path /var/www/shop --alert-channel 123
  project.py show my-site
  project.py check my-site            # run an uptime probe now
  project.py set-config 2 uptime_interval_sec 120
  project.py set-tier 2 deep --reset  # reseed agent-plane intervals to the tier
  project.py pause 2 | resume 2 | archive 2
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from evergreen import uptime
from evergreen.db import (
    create_project,
    get_project,
    get_project_by_slug,
    list_projects,
    set_project_status,
    update_project_config,
)
from evergreen.tiers import DEPTH_TIERS, PROJECT_TYPES, seed_project_cron

# config keys that matter per type — onboarding collects these.
TYPE_CONFIG_KEYS = {
    "code_db": ["project_path", "project_path_interactive", "ssh_staging", "ssh_prod"],
    "wordpress": ["ssh", "wp_path"],
    "static": [],
}


def _resolve(ident: str) -> dict:
    p = get_project_by_slug(ident)
    if not p and ident.isdigit():
        p = get_project(int(ident))
    if not p:
        sys.exit(f"No project matching '{ident}'")
    return p


def _print_project(p: dict, with_status: bool = True):
    print(f"#{p['id']}  {p['name']}  [{p['type']} / {p['depth_tier']} / {p['status']}]")
    if p["base_url"]:
        print(f"    url:    {p['base_url']}")
    if p["config"]:
        print(f"    config: {json.dumps(p['config'])}")
    from evergreen.db import get_connection
    conn = get_connection()
    sched = dict(conn.execute(
        "SELECT skill, interval_minutes FROM cron_jobs WHERE project_id = ? AND enabled = 1",
        (p["id"],),
    ).fetchall())
    print(f"    agent skills: {sched or '(monitoring-plane only)'}")
    if with_status:
        row = conn.execute(
            "SELECT is_up, last_status_code, last_response_time_ms, ssl_expiry_days, last_checked_at "
            "FROM project_status WHERE project_id = ?", (p["id"],)
        ).fetchone()
        if row:
            up = "UP" if row[0] else "DOWN"
            print(f"    uptime: {up}  http={row[1]}  {row[2]}ms  ssl={row[3]}d  checked_at={row[4]}")
    conn.close()


def cmd_list(args):
    projects = list_projects()
    if not projects:
        print("No projects yet. Add one with: project.py add ...")
        return
    for p in projects:
        _print_project(p)
        print()


def cmd_add(args):
    config = {}
    for key in ("project_path", "project_path_interactive", "ssh_staging", "ssh_prod"):
        v = getattr(args, key, None)
        if v:
            config[key] = v
    if args.ssh:
        config["ssh"] = args.ssh
    if args.wp_path:
        config["wp_path"] = args.wp_path
    if args.alert_channel:
        config["discord_channel_id"] = args.alert_channel
    if args.failure_threshold is not None:
        config["failure_threshold"] = args.failure_threshold

    pid = create_project(
        name=args.name, type=args.type, base_url=args.base_url,
        depth_tier=args.tier, config=config,
    )
    seed_project_cron(pid, args.type, args.tier)
    print(f"Created project #{pid}: {args.name}")

    p = get_project(pid)
    if p["base_url"]:
        print("Running baseline uptime check...")
        result = uptime.check_url(p["base_url"])
        from evergreen.db import get_connection
        conn = get_connection()
        uptime.record_check(conn, p, result)
        conn.commit()
        conn.close()
        verdict = "reachable" if result["is_up"] else f"NOT reachable ({result['error'] or result['status_code']})"
        print(f"  {p['base_url']} -> {verdict} (HTTP {result['status_code']}, {result['response_time_ms']}ms, ssl {result['ssl_expiry_days']}d)")
    print()
    _print_project(get_project(pid))


def cmd_show(args):
    _print_project(_resolve(args.ident))


def cmd_check(args):
    p = _resolve(args.ident)
    if not p["base_url"]:
        sys.exit(f"{p['name']} has no base_url to check")
    result = uptime.check_url(p["base_url"])
    from evergreen.db import get_connection
    conn = get_connection()
    uptime.record_check(conn, p, result)
    conn.commit()
    conn.close()
    print(json.dumps(result, indent=2))


def cmd_set_config(args):
    p = _resolve(args.ident)
    val = args.value
    if val.isdigit():
        val = int(val)
    elif val.lower() in ("true", "false"):
        val = val.lower() == "true"
    cfg = update_project_config(p["id"], **{args.key: val})
    print(f"#{p['id']} config: {json.dumps(cfg)}")


def cmd_set_tier(args):
    p = _resolve(args.ident)
    from evergreen.db import get_connection
    conn = get_connection()
    conn.execute("UPDATE projects SET depth_tier = ? WHERE id = ?", (args.tier, p["id"]))
    conn.commit()
    conn.close()
    sched = seed_project_cron(p["id"], p["type"], args.tier, reset=args.reset)
    print(f"#{p['id']} tier -> {args.tier}; agent schedule: {sched or '(none)'}")


def cmd_status_change(args, status):
    p = _resolve(args.ident)
    set_project_status(p["id"], status)
    print(f"#{p['id']} {p['name']} -> {status}")


def cmd_delete(args):
    p = _resolve(args.ident)
    if p["id"] == 1:
        sys.exit("Refusing to delete project 1 (the original instance project).")
    from evergreen.db import get_connection
    conn = get_connection()
    for t in ("uptime_checks", "project_status", "cron_jobs", "skill_queue",
              "bugs", "security_alerts", "runs", "themes"):
        conn.execute(f"DELETE FROM {t} WHERE project_id = ?", (p["id"],))
    conn.execute("DELETE FROM projects WHERE id = ?", (p["id"],))
    conn.commit()
    conn.close()
    print(f"Deleted project #{p['id']} ({p['name']}) and all its rows.")


def main():
    ap = argparse.ArgumentParser(description="Manage evergreen projects")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list").set_defaults(func=cmd_list)

    a = sub.add_parser("add")
    a.add_argument("--name", required=True)
    a.add_argument("--type", required=True, choices=PROJECT_TYPES)
    a.add_argument("--base-url", dest="base_url")
    a.add_argument("--tier", default="standard", choices=DEPTH_TIERS)
    a.add_argument("--project-path", dest="project_path")
    a.add_argument("--project-path-interactive", dest="project_path_interactive")
    a.add_argument("--ssh-staging", dest="ssh_staging")
    a.add_argument("--ssh-prod", dest="ssh_prod")
    a.add_argument("--ssh", help="SSH alias for wordpress wp-cli access")
    a.add_argument("--wp-path", dest="wp_path")
    a.add_argument("--alert-channel", dest="alert_channel", help="per-project Discord channel id")
    a.add_argument("--failure-threshold", dest="failure_threshold", type=int)
    a.set_defaults(func=cmd_add)

    s = sub.add_parser("show"); s.add_argument("ident"); s.set_defaults(func=cmd_show)
    c = sub.add_parser("check"); c.add_argument("ident"); c.set_defaults(func=cmd_check)
    dl = sub.add_parser("delete"); dl.add_argument("ident"); dl.set_defaults(func=cmd_delete)

    sc = sub.add_parser("set-config")
    sc.add_argument("ident"); sc.add_argument("key"); sc.add_argument("value")
    sc.set_defaults(func=cmd_set_config)

    st = sub.add_parser("set-tier")
    st.add_argument("ident"); st.add_argument("tier", choices=DEPTH_TIERS)
    st.add_argument("--reset", action="store_true", help="overwrite tuned intervals back to tier defaults")
    st.set_defaults(func=cmd_set_tier)

    for name, status in (("pause", "paused"), ("resume", "active"), ("archive", "archived")):
        ps = sub.add_parser(name); ps.add_argument("ident")
        ps.set_defaults(func=lambda args, _s=status: cmd_status_change(args, _s))

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
