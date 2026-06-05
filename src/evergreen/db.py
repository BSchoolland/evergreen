import sqlite3
import time
from importlib.resources import files
from pathlib import Path

EVERGREEN_DIR = Path.home() / ".evergreen"
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


def init_db() -> sqlite3.Connection:
    EVERGREEN_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    _migrate_text_timestamps(conn)
    _migrate_verify_bug(conn)
    conn.executescript(_read_schema())
    conn.commit()
    return conn


def get_connection() -> sqlite3.Connection:
    if not DB_PATH.exists():
        return init_db()
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn
