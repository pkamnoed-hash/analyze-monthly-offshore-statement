"""Manual, on-demand backup of the real Turso database into a local SQLite file
under data/backups/ (gitignored). Read-only against Turso -- it only issues
SELECT/PRAGMA statements, never writes.

The System Backup page can't do this job: it backs up data/portfolio.db, a
frozen pre-Turso snapshot the running app no longer reads (see
docs/BACKUP_AND_TESTING.md).

Usage:
    python scripts/backup_turso.py
        backs up whatever .streamlit/secrets.toml points at (dev, normally)
    python scripts/backup_turso.py --secrets .streamlit/secrets.prod.toml.bak
        backs up the real prod database using the saved prod credentials

The file is named bk-turso-<env>-<version>-<ddmmyy>-<hhmm>.db, where <env> comes
from APP_ENV in the secrets file (prod if absent). Check the "Backing up <host>"
line the script prints before trusting the label.
"""

import argparse
import os
import sys
import tomllib
from urllib.parse import urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def main():
    parser = argparse.ArgumentParser(description="Back up the Turso database to a local SQLite file.")
    parser.add_argument(
        "--secrets", default=os.path.join(ROOT, ".streamlit", "secrets.toml"),
        help="TOML file holding TURSO_DATABASE_URL / TURSO_AUTH_TOKEN (default: .streamlit/secrets.toml)",
    )
    args = parser.parse_args()

    with open(args.secrets, "rb") as f:
        secrets = tomllib.load(f)
    # Assigned, not setdefault: --secrets must win over any Turso values already in the environment.
    os.environ["TURSO_DATABASE_URL"] = secrets["TURSO_DATABASE_URL"]
    os.environ["TURSO_AUTH_TOKEN"] = secrets["TURSO_AUTH_TOKEN"]

    env_label = secrets.get("APP_ENV", "prod")
    host = urlparse(secrets["TURSO_DATABASE_URL"]).hostname
    print(f"Backing up {host} (APP_ENV = {env_label}) ...")

    from core import backup, db  # noqa: E402  (needs the env bridge above first)

    conn = db.get_connection()
    try:
        filename, counts = backup.backup_turso_database(conn, env_label=env_label)
    finally:
        conn.close()

    path = os.path.join(backup.DEFAULT_BACKUP_DIR, filename)
    print(f"\nSaved {path} ({os.path.getsize(path):,} bytes)\n")
    width = max(len(name) for name in counts)
    for name, rows in counts.items():
        print(f"  {name:<{width}}  {rows:>6,} rows")
    print(f"\n{len(counts)} tables, {sum(counts.values()):,} rows in total.")
    print("This file holds your real data (including the app login hash) -- keep it private.")


if __name__ == "__main__":
    main()
