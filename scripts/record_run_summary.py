#!/usr/bin/env python3
"""Record a summary for a cron run."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from evergreen.db import get_connection

if len(sys.argv) < 3:
    print("Usage: record_run_summary.py <run_id> <summary>", file=sys.stderr)
    sys.exit(1)

run_id = int(sys.argv[1])
summary = sys.argv[2]

conn = get_connection()
conn.execute("UPDATE runs SET summary = ? WHERE id = ?", (summary, run_id))
conn.commit()
conn.close()
print(f"Updated run {run_id} summary.")
