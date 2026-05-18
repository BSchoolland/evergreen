import sqlite3
from importlib.resources import files
from pathlib import Path

EVERGREEN_DIR = Path.home() / ".evergreen"
DB_PATH = EVERGREEN_DIR / "evergreen.db"
CONFIG_PATH = EVERGREEN_DIR / "config"


def is_configured() -> bool:
    return CONFIG_PATH.exists()


def get_cli() -> str:
    return CONFIG_PATH.read_text().strip()


def _read_schema() -> str:
    return files("evergreen").joinpath("schema.sql").read_text()


def init_db() -> sqlite3.Connection:
    EVERGREEN_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
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
