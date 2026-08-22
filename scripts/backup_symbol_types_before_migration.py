"""One-off, read-only snapshot of the real Turso symbol_types table, taken
right before core/db.py's _migrate_symbol_types_open_category() runs against
it for real (automatically, the next time the app starts -- see
dashboard_app.py's init_db() call). data/portfolio.db is NOT a substitute for
this: core/db.py's own get_connection() targets Turso exclusively now, so
that local file is stale and CLAUDE.md's usual "back it up first" advice
doesn't cover this specific migration.

Issues a single SELECT against symbol_types -- never writes to Turso.

Usage:
    python scripts/backup_symbol_types_before_migration.py
"""

import json
import os
import sys
import tomllib
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

SECRETS_PATH = os.path.join(ROOT, ".streamlit", "secrets.toml")
BACKUP_DIR = os.path.join(ROOT, "data", "backups")


def _bridge_turso_secrets():
    """core/db.py deliberately has no Streamlit import (see CLAUDE.md), so it reads
    Turso connection details from the environment -- same bridging dashboard_app.py
    does from st.secrets, done here directly from secrets.toml since this script runs
    outside Streamlit entirely."""
    with open(SECRETS_PATH, "rb") as f:
        secrets = tomllib.load(f)
    os.environ.setdefault("TURSO_DATABASE_URL", secrets["TURSO_DATABASE_URL"])
    os.environ.setdefault("TURSO_AUTH_TOKEN", secrets["TURSO_AUTH_TOKEN"])


def main():
    _bridge_turso_secrets()
    from core import db  # noqa: E402  (needs the env bridge above first)

    conn = db.get_connection()
    try:
        rows = conn.execute(
            "SELECT symbol, allocation_type, updated_at FROM symbol_types ORDER BY symbol"
        ).fetchall()
    finally:
        conn.close()

    os.makedirs(BACKUP_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%d%m%y-%H%M")
    dest = os.path.join(BACKUP_DIR, f"symbol_types-pre-open-category-migration-{timestamp}.json")
    payload = [{"symbol": r[0], "allocation_type": r[1], "updated_at": r[2]} for r in rows]
    with open(dest, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    print(f"Backed up {len(payload)} symbol_types row(s) to {dest}")


if __name__ == "__main__":
    main()
