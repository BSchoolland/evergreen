import json
import os
import re
import sqlite3
import time
from importlib.resources import files
from pathlib import Path

# State dir is per-INSTANCE. $EVERGREEN_HOME lets several fully-isolated instances
# (e.g. work vs personal) run on one machine, each with its own DB, config, Discord
# bot and credentials — sharing nothing. Defaults to ~/.evergreen.
EVERGREEN_DIR = Path(os.environ.get("EVERGREEN_HOME") or (Path.home() / ".evergreen"))
DB_PATH = EVERGREEN_DIR / "evergreen.db"
CONFIG_PATH = EVERGREEN_DIR / "config"


def is_configured() -> bool:
    return CONFIG_PATH.exists()


def get_cli() -> str:
    return CONFIG_PATH.read_text().strip()


def epoch() -> int:
    return int(time.time())


def _read_schema() -> str:
    return files("evergreen").joinpath("schema.sql").read_text()


def _needs_ts_migration(conn: sqlite3.Connection) -> bool:
    """Check if any timestamp column still has text type affinity."""
    try:
        row = conn.execute(
            "SELECT typeof(created_at) FROM bugs WHERE created_at IS NOT NULL LIMIT 1"
        ).fetchone()
    except sqlite3.OperationalError:
        return False
    return row is not None and row[0] == "text"


def _migrate_text_timestamps(conn: sqlite3.Connection):
    """Rebuild tables so timestamp columns have INTEGER affinity with epoch-second values.

    Handles both ISO 8601 ('2026-05-18T14:30:00') and SQLite text ('2026-05-18 14:30:00').
    Values that strftime can't parse are replaced with the current epoch.
    """
    if not _needs_ts_migration(conn):
        return

    schema = _read_schema()

    tables_to_migrate = [
        "bugs", "security_alerts", "discord_messages",
        "runs", "cron_jobs", "skill_queue",
    ]

    ts_columns = {
        "bugs": ["created_at", "first_seen_at", "last_seen_at", "resolved_at"],
        "security_alerts": ["created_at", "resolved_at"],
        "discord_messages": ["created_at", "read_at"],
        "runs": ["started_at", "finished_at"],
        "cron_jobs": ["last_run_at"],
        "skill_queue": ["queued_at", "started_at"],
    }

    for table in tables_to_migrate:
        try:
            cols_info = conn.execute(f"PRAGMA table_info({table})").fetchall()
        except sqlite3.OperationalError:
            continue
        if not cols_info:
            continue

        col_names = [c[1] for c in cols_info]

        # Normalize ISO 'T' separators so strftime can parse
        for col in ts_columns.get(table, []):
            if col not in col_names:
                continue
            conn.execute(
                f"UPDATE {table} SET {col} = replace({col}, 'T', ' ') "
                f"WHERE {col} IS NOT NULL AND typeof({col}) = 'text' AND {col} LIKE '%T%'"
            )

        # Convert text timestamps to integer epoch seconds
        for col in ts_columns.get(table, []):
            if col not in col_names:
                continue
            conn.execute(
                f"UPDATE {table} SET {col} = coalesce("
                f"  cast(strftime('%s', {col}) as integer),"
                f"  cast(strftime('%s', 'now') as integer)"
                f") WHERE {col} IS NOT NULL AND typeof({col}) = 'text'"
            )

        # Rebuild table: rename old, create new with INTEGER columns, copy data, drop old
        conn.execute(f"ALTER TABLE {table} RENAME TO _old_{table}")

    conn.commit()
    conn.executescript(schema)
    conn.commit()

    for table in tables_to_migrate:
        try:
            old_cols = conn.execute(f"PRAGMA table_info(_old_{table})").fetchall()
        except sqlite3.OperationalError:
            continue
        if not old_cols:
            continue

        new_cols = conn.execute(f"PRAGMA table_info({table})").fetchall()
        new_col_names = {c[1] for c in new_cols}
        shared = [c[1] for c in old_cols if c[1] in new_col_names]
        cols_str = ", ".join(shared)

        conn.execute(f"INSERT OR IGNORE INTO {table} ({cols_str}) SELECT {cols_str} FROM _old_{table}")
        conn.execute(f"DROP TABLE _old_{table}")

    conn.commit()


def _migrate_verify_bug(conn: sqlite3.Connection):
    """Add verification columns, rename root_cause, and update status CHECK."""
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(bugs)").fetchall()]
    except sqlite3.OperationalError:
        return
    if not cols or "root_cause" not in cols:
        return
    conn.execute("ALTER TABLE bugs RENAME TO _old_bugs")
    schema = _read_schema()
    for line in schema.split(";"):
        if "CREATE TABLE IF NOT EXISTS bugs" in line:
            conn.execute(line + ";")
            break
    old_cols = [r[1] for r in conn.execute("PRAGMA table_info(_old_bugs)").fetchall()]
    new_cols = [r[1] for r in conn.execute("PRAGMA table_info(bugs)").fetchall()]
    col_map = {c: c for c in old_cols if c in new_cols}
    col_map["root_cause"] = "probable_root_cause"
    src = ", ".join(col_map.keys())
    dst = ", ".join(col_map.values())
    conn.execute(f"INSERT INTO bugs ({dst}) SELECT {src} FROM _old_bugs")
    conn.execute("DROP TABLE _old_bugs")
    conn.commit()


def _migrate_disposition(conn: sqlite3.Connection):
    """Add disposition_reason/dismissed_by columns and the 'backlog' status to
    bugs and security_alerts by rebuilding them from the current schema."""
    schema = _read_schema()
    for table in ("bugs", "security_alerts"):
        try:
            cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
        except sqlite3.OperationalError:
            continue
        if not cols or "disposition_reason" in cols:
            continue
        conn.execute(f"ALTER TABLE {table} RENAME TO _old_{table}")
        for stmt in schema.split(";"):
            if f"CREATE TABLE IF NOT EXISTS {table}" in stmt:
                conn.execute(stmt + ";")
                break
        old_cols = [r[1] for r in conn.execute(f"PRAGMA table_info(_old_{table})").fetchall()]
        new_cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        shared = ", ".join(c for c in old_cols if c in new_cols)
        conn.execute(f"INSERT INTO {table} ({shared}) SELECT {shared} FROM _old_{table}")
        conn.execute(f"DROP TABLE _old_{table}")
    conn.commit()


def _migrate_action_needed(conn: sqlite3.Connection):
    """Add the 'action_needed' status to bugs/security_alerts (CHECK rebuild)."""
    schema = _read_schema()
    for table in ("bugs", "security_alerts"):
        try:
            row = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
        except sqlite3.OperationalError:
            continue
        if not row or not row[0] or "action_needed" in row[0]:
            continue
        conn.execute(f"ALTER TABLE {table} RENAME TO _old_{table}")
        for stmt in schema.split(";"):
            if f"CREATE TABLE IF NOT EXISTS {table}" in stmt:
                conn.execute(stmt + ";")
                break
        old_cols = [r[1] for r in conn.execute(f"PRAGMA table_info(_old_{table})").fetchall()]
        new_cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        shared = ", ".join(c for c in old_cols if c in new_cols)
        conn.execute(f"INSERT INTO {table} ({shared}) SELECT {shared} FROM _old_{table}")
        conn.execute(f"DROP TABLE _old_{table}")
    conn.commit()


def _migrate_run_cost(conn: sqlite3.Connection):
    """Add cost/tokens/model/effort columns to an existing runs table (CREATE IF
    NOT EXISTS won't add columns to a table that already exists)."""
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(runs)").fetchall()]
    except sqlite3.OperationalError:
        return
    if not cols:
        return
    for name, decl in (("cost", "REAL"), ("tokens", "INTEGER"),
                       ("model", "TEXT"), ("effort", "TEXT")):
        if name not in cols:
            conn.execute(f"ALTER TABLE runs ADD COLUMN {name} {decl}")
    conn.commit()


def _migrate_run_review_links(conn: sqlite3.Connection):
    """Add review-run linkage columns to existing runs tables."""
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(runs)").fetchall()]
    except sqlite3.OperationalError:
        return
    if not cols:
        return
    for name, decl in (
        ("parent_run_id", "INTEGER REFERENCES runs(id) ON DELETE SET NULL"),
        ("branch", "TEXT"),
        ("pr_url", "TEXT"),
    ):
        if name not in cols:
            conn.execute(f"ALTER TABLE runs ADD COLUMN {name} {decl}")
    conn.commit()


def _migrate_skill_queue(conn: sqlite3.Connection):
    """Add source/force columns to an existing skill_queue table (for manually
    triggered skill runs)."""
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(skill_queue)").fetchall()]
    except sqlite3.OperationalError:
        return
    if not cols:
        return
    if "source" not in cols:
        conn.execute("ALTER TABLE skill_queue ADD COLUMN source TEXT NOT NULL DEFAULT 'cron'")
    if "force" not in cols:
        conn.execute("ALTER TABLE skill_queue ADD COLUMN force INTEGER NOT NULL DEFAULT 0")
    conn.commit()


def _migrate_project_spine(conn: sqlite3.Connection):
    """Turn a single-project DB into a multi-project one.

    Idempotent: keyed on bugs.project_id existing. Creates the `projects` table,
    seeds project 1 from the legacy global `configs` (the original TACOS project),
    backfills `project_id = 1` onto every per-project table, and rebuilds
    `cron_jobs` with a composite (project_id, skill) primary key.
    """
    try:
        bug_cols = [r[1] for r in conn.execute("PRAGMA table_info(bugs)").fetchall()]
    except sqlite3.OperationalError:
        return
    if not bug_cols:
        return  # fresh DB — schema.sql creates everything already shaped

    # NOTE: we can't gate the whole migration on "project_id in bugs" — an earlier
    # table-rebuild migration (_migrate_disposition/_migrate_action_needed) recreates
    # bugs from schema.sql, which already declares project_id, so the column can be
    # present before this runs. Each step below self-gates instead.

    # 1. projects table (schema.sql also defines it; create here so the backfill
    #    below can reference it on an existing DB before executescript runs).
    conn.execute(
        """CREATE TABLE IF NOT EXISTS projects (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          slug TEXT NOT NULL UNIQUE,
          name TEXT NOT NULL,
          type TEXT NOT NULL DEFAULT 'static' CHECK(type IN ('code_db', 'wordpress', 'static')),
          status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'paused', 'archived')),
          base_url TEXT,
          depth_tier TEXT NOT NULL DEFAULT 'standard',
          config TEXT NOT NULL DEFAULT '{}',
          created_at INTEGER NOT NULL DEFAULT (cast(strftime('%s', 'now') as integer))
        )"""
    )

    # 2. Seed project 1 from the legacy global configs (the pre-multi-project
    #    setup), only if it isn't there yet.
    if not conn.execute("SELECT 1 FROM projects WHERE id = 1").fetchone():
        cfg = dict(conn.execute("SELECT key, value FROM configs").fetchall())
        name = cfg.get("project_name", "Project 1")
        slug = _slugify(name)
        project_config = {
            k: cfg[k]
            for k in ("project_path", "project_path_interactive", "ssh_staging",
                      "ssh_prod", "discord_channel_id")
            if k in cfg
        }
        base_url = cfg.get("base_url")
        conn.execute(
            "INSERT OR IGNORE INTO projects (id, slug, name, type, status, base_url, depth_tier, config) "
            "VALUES (1, ?, ?, 'code_db', 'active', ?, 'deep', ?)",
            (slug, name, base_url, json.dumps(project_config)),
        )

    # 3. Backfill project_id = 1 onto every per-project table (constant default is
    #    allowed by ALTER ADD COLUMN and backfills existing rows in one shot).
    for table in ("bugs", "security_alerts", "runs", "conversations", "skill_queue"):
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
        if cols and "project_id" not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN project_id INTEGER NOT NULL DEFAULT 1")

    # runs also gains input/output token split for notional pricing.
    run_cols = [r[1] for r in conn.execute("PRAGMA table_info(runs)").fetchall()]
    for name_, decl in (("input_tokens", "INTEGER"), ("output_tokens", "INTEGER")):
        if name_ not in run_cols:
            conn.execute(f"ALTER TABLE runs ADD COLUMN {name_} {decl}")

    # 4. Rebuild cron_jobs: PK skill -> PK (project_id, skill), + model/effort.
    cj_cols = [r[1] for r in conn.execute("PRAGMA table_info(cron_jobs)").fetchall()]
    if cj_cols and "project_id" not in cj_cols:
        conn.execute("ALTER TABLE cron_jobs RENAME TO _old_cron_jobs")
        conn.execute(
            """CREATE TABLE cron_jobs (
              project_id INTEGER NOT NULL DEFAULT 1,
              skill TEXT NOT NULL,
              interval_minutes INTEGER NOT NULL,
              enabled INTEGER NOT NULL DEFAULT 1,
              last_run_at INTEGER,
              model TEXT,
              effort TEXT,
              PRIMARY KEY (project_id, skill)
            )"""
        )
        conn.execute(
            "INSERT INTO cron_jobs (project_id, skill, interval_minutes, enabled, last_run_at) "
            "SELECT 1, skill, interval_minutes, enabled, last_run_at FROM _old_cron_jobs"
        )
        conn.execute("DROP TABLE _old_cron_jobs")

    conn.commit()


def init_db() -> sqlite3.Connection:
    EVERGREEN_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    _migrate_text_timestamps(conn)
    _migrate_verify_bug(conn)
    _migrate_disposition(conn)
    _migrate_action_needed(conn)
    _migrate_run_cost(conn)
    _migrate_run_review_links(conn)
    _migrate_skill_queue(conn)
    _migrate_project_spine(conn)
    conn.executescript(_read_schema())
    _seed_model_pricing(conn)
    conn.commit()
    return conn


def get_connection() -> sqlite3.Connection:
    if not DB_PATH.exists():
        return init_db()
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# --- Config helpers ---

def get_config(key: str, default: str | None = None) -> str | None:
    conn = get_connection()
    row = conn.execute("SELECT value FROM configs WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row[0] if row else default


def set_config(key: str, value: str) -> None:
    conn = get_connection()
    conn.execute(
        "INSERT OR REPLACE INTO configs (key, value) VALUES (?, ?)",
        (key, str(value)),
    )
    conn.commit()
    conn.close()


def get_project_path() -> str | None:
    """Resolve the target-repo path for the *current actor*.

    Evergreen runs as two "devs" that share this machine and DB but never share a
    working tree: the watchdog uses the `project_path` clone, the interactive dev
    uses the `project_path_interactive` clone. Each actor sets EVERGREEN_PROJECT_PATH
    in its environment to point at its own clone; that env var overrides the configs
    value. With no env override, falls back to the current project's `project_path`.
    """
    env = os.environ.get("EVERGREEN_PROJECT_PATH")
    if env:
        return env
    pid = current_project_id()
    if pid is not None:
        path = project_config_value(pid, "project_path")
        if path:
            return path
    return get_config("project_path")


# --- Projects ---

def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "project"


def _row_to_project(row) -> dict:
    keys = ("id", "slug", "name", "type", "status", "base_url",
            "depth_tier", "config", "created_at")
    p = dict(zip(keys, row))
    try:
        p["config"] = json.loads(p["config"]) if p["config"] else {}
    except (json.JSONDecodeError, TypeError):
        p["config"] = {}
    return p


_PROJECT_COLS = "id, slug, name, type, status, base_url, depth_tier, config, created_at"


def current_project_id() -> int | None:
    """The project the current skill run is scoped to, from EVERGREEN_PROJECT_ID."""
    val = os.environ.get("EVERGREEN_PROJECT_ID")
    if val and val.isdigit():
        return int(val)
    return None


def list_projects(include_inactive: bool = True) -> list[dict]:
    conn = get_connection()
    sql = f"SELECT {_PROJECT_COLS} FROM projects"
    if not include_inactive:
        sql += " WHERE status = 'active'"
    sql += " ORDER BY id"
    rows = conn.execute(sql).fetchall()
    conn.close()
    return [_row_to_project(r) for r in rows]


def get_project(project_id: int) -> dict | None:
    conn = get_connection()
    row = conn.execute(
        f"SELECT {_PROJECT_COLS} FROM projects WHERE id = ?", (project_id,)
    ).fetchone()
    conn.close()
    return _row_to_project(row) if row else None


def get_project_by_slug(slug: str) -> dict | None:
    conn = get_connection()
    row = conn.execute(
        f"SELECT {_PROJECT_COLS} FROM projects WHERE slug = ?", (slug,)
    ).fetchone()
    conn.close()
    return _row_to_project(row) if row else None


def create_project(name: str, type: str, base_url: str | None = None,
                   depth_tier: str = "standard", config: dict | None = None,
                   slug: str | None = None) -> int:
    slug = slug or _slugify(name)
    conn = get_connection()
    # Disambiguate slug collisions (e.g. two "portfolio" projects).
    base_slug, n = slug, 2
    while conn.execute("SELECT 1 FROM projects WHERE slug = ?", (slug,)).fetchone():
        slug = f"{base_slug}-{n}"
        n += 1
    cur = conn.execute(
        "INSERT INTO projects (slug, name, type, base_url, depth_tier, config) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (slug, name, type, base_url, depth_tier, json.dumps(config or {})),
    )
    conn.commit()
    pid = cur.lastrowid
    conn.close()
    return pid


def set_project_status(project_id: int, status: str) -> None:
    conn = get_connection()
    conn.execute("UPDATE projects SET status = ? WHERE id = ?", (status, project_id))
    conn.commit()
    conn.close()


def set_project_config(project_id: int, config: dict) -> None:
    """Replace a project's whole config JSON blob."""
    conn = get_connection()
    conn.execute(
        "UPDATE projects SET config = ? WHERE id = ?",
        (json.dumps(config), project_id),
    )
    conn.commit()
    conn.close()


def update_project_config(project_id: int, **kwargs) -> dict:
    """Merge keys into a project's config JSON (None deletes a key)."""
    p = get_project(project_id)
    cfg = dict(p["config"]) if p else {}
    for k, v in kwargs.items():
        if v is None:
            cfg.pop(k, None)
        else:
            cfg[k] = v
    set_project_config(project_id, cfg)
    return cfg


def project_config_value(project_id: int, key: str, default=None):
    p = get_project(project_id)
    if not p:
        return default
    return p["config"].get(key, default)


def project_alert_channel(project_id: int) -> str | None:
    """Per-project Discord channel, falling back to the instance default."""
    return project_config_value(project_id, "discord_channel_id") or get_config("discord_channel_id")


# --- Notional model pricing ---

# Dollars per 1M tokens (input, output). Notional — tune freely in the DB. These
# are seeded once with INSERT OR IGNORE so manual edits are never clobbered.
_DEFAULT_PRICING = {
    "gpt-5.5": (1.25, 10.0),
    "gpt-5.4-mini": (0.25, 2.0),
    "opus": (15.0, 75.0),
    "sonnet": (3.0, 15.0),
    "haiku": (0.80, 4.0),
    "moonshotai/kimi-k2.6": (0.60, 2.5),
}


def _seed_model_pricing(conn: sqlite3.Connection):
    for model, (inp, out) in _DEFAULT_PRICING.items():
        conn.execute(
            "INSERT OR IGNORE INTO model_pricing (model, input_per_mtok, output_per_mtok) "
            "VALUES (?, ?, ?)",
            (model, inp, out),
        )


def get_model_price(model: str | None) -> tuple[float, float] | None:
    if not model:
        return None
    conn = get_connection()
    row = conn.execute(
        "SELECT input_per_mtok, output_per_mtok FROM model_pricing WHERE model = ?",
        (model,),
    ).fetchone()
    # pi records provider-prefixed ids (e.g. "openai-codex/gpt-5.4-mini"); the
    # pricing keys are bare. Fall back to the suffix so both engines resolve.
    if not row and "/" in model:
        row = conn.execute(
            "SELECT input_per_mtok, output_per_mtok FROM model_pricing WHERE model = ?",
            (model.rsplit("/", 1)[-1],),
        ).fetchone()
    conn.close()
    return (row[0], row[1]) if row else None


def compute_cost(model: str | None, input_tokens: int | None,
                 output_tokens: int | None, total_tokens: int | None) -> float | None:
    """Notional cost from the pricing table. Returns None if the model isn't
    priced (caller may fall back to the provider-reported cost). When the
    input/output split is unknown, blends the two rates over the total."""
    price = get_model_price(model)
    if price is None:
        return None
    in_rate, out_rate = price
    if input_tokens is not None and output_tokens is not None:
        return round(input_tokens / 1e6 * in_rate + output_tokens / 1e6 * out_rate, 6)
    if total_tokens:
        blended = (in_rate + out_rate) / 2
        return round(total_tokens / 1e6 * blended, 6)
    return 0.0
