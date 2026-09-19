"""Manual, on-demand backups of the two live files most at risk from an
accident (a bad git rollback, a bad edit, a bad statement import):
data/portfolio.db and the official Statement xlsx. Kept free of Streamlit
imports, matching every other core/ module, so it can be unit tested in
isolation (see tests/test_backup.py, which never touches the real data/
files -- everything runs against a temp directory).

Restore is deliberately not implemented here -- this first pass is
backup-only, per the confirmed scope; a restore action can be added later
once backup itself is built and trusted.
"""

import glob
import json
import os
import re
import shutil
import sqlite3
import tempfile
from datetime import datetime

import pandas as pd

from core.version import current_app_version

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB_PATH = os.path.join(_PROJECT_ROOT, "data", "portfolio.db")
DEFAULT_STATEMENT_GLOB = os.path.join(_PROJECT_ROOT, "data", "Offshore_Statements_*.xlsx")
DEFAULT_BACKUP_DIR = os.path.join(_PROJECT_ROOT, "data", "backups")

_DB_PREFIX = "bk-portfolio"
_STATEMENT_PREFIX = "bk-statements"
_SOURCE_STATEMENT_RE = re.compile(r"Offshore_Statements_(.+)\.xlsx$")
_TIMESTAMP_SUFFIX_RE = re.compile(r"^(.*)-(\d{6}-\d{4})$")
_DATE_RANGE_RE = re.compile(r"^(.*)-(\d{4}-\d{2}_to_\d{4}-\d{2})$")
_MANIFEST_FILENAME = "manifest.json"


def _manifest_path(backup_dir: str) -> str:
    return os.path.join(backup_dir, _MANIFEST_FILENAME)


def _load_manifest(backup_dir: str) -> dict:
    """filename -> free-text note, e.g. {"bk-portfolio-v2.3-290726-1623.db":
    "before testing rebalance"}. A separate sidecar file rather than
    embedding notes in the filename itself -- arbitrary user text isn't
    safe/parseable as part of a filename already carrying type/version/
    date-range/timestamp fields. Missing or corrupt manifest -> empty dict,
    never an error (a note is a convenience, not load-bearing)."""
    path = _manifest_path(backup_dir)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save_manifest(backup_dir: str, manifest: dict) -> None:
    with open(_manifest_path(backup_dir), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)


def _record_note(backup_dir: str, filename: str, note: str) -> None:
    if not note:
        return
    manifest = _load_manifest(backup_dir)
    manifest[filename] = note
    _save_manifest(backup_dir, manifest)


def backup_database(
    source_path: str = DEFAULT_DB_PATH,
    backup_dir: str = DEFAULT_BACKUP_DIR,
    *,
    timestamp: datetime | None = None,
    version: str | None = None,
    note: str = "",
) -> str:
    """Uses SQLite's own online-backup API (sqlite3.Connection.backup()),
    not a raw file copy -- guarantees a consistent snapshot even if
    something else has the db open, unlike shutil.copy which can grab a
    torn/inconsistent read mid-write. The source connection is opened
    read-only (mode=ro) so backing up can never itself write to the live
    db. An optional free-text note is recorded in a manifest.json sidecar
    inside backup_dir, keyed by the resulting filename -- not embedded in
    the filename itself. Returns the created filename (not the full
    path)."""
    timestamp = timestamp or datetime.now()
    version = version if version is not None else current_app_version()
    os.makedirs(backup_dir, exist_ok=True)

    filename = f"{_DB_PREFIX}-{version}-{timestamp.strftime('%d%m%y-%H%M')}.db"
    dest_path = os.path.join(backup_dir, filename)

    source_conn = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
    dest_conn = sqlite3.connect(dest_path)
    try:
        source_conn.backup(dest_conn)
    finally:
        dest_conn.close()
        source_conn.close()

    _record_note(backup_dir, filename, note)
    return filename


def backup_statement_file(
    source_glob: str = DEFAULT_STATEMENT_GLOB,
    backup_dir: str = DEFAULT_BACKUP_DIR,
    *,
    timestamp: datetime | None = None,
    version: str | None = None,
    note: str = "",
) -> str:
    """source_glob matches data/Offshore_Statements_*.xlsx (not a
    hardcoded exact filename) -- this file is periodically replaced with a
    new date-range name whenever a new month's official statement arrives,
    and a glob means this function keeps working across that rename
    without a matching code edit each time, unlike dashboard.py/
    reconciliation.py's own hardcoded paths. Raises FileNotFoundError if
    nothing matches, ValueError if more than one file matches (ambiguous
    -- surface to the caller rather than silently guessing). Plain
    shutil.copy2 (static file, no live-connection concern, unlike the db).
    Extracts the date-range portion of the source filename (e.g.
    "2023-01_to_2026-06") into the backup name -- that range is this
    file's own real identity. note is recorded the same way as
    backup_database()'s. Returns the created filename (not the full
    path)."""
    matches = glob.glob(source_glob)
    if not matches:
        raise FileNotFoundError(f"No file matched {source_glob!r} -- nothing to back up.")
    if len(matches) > 1:
        raise ValueError(f"{len(matches)} files matched {source_glob!r}, expected exactly one: {matches}")
    source_path = matches[0]

    name_match = _SOURCE_STATEMENT_RE.search(os.path.basename(source_path))
    date_range = name_match.group(1) if name_match else "unknown-range"

    timestamp = timestamp or datetime.now()
    version = version if version is not None else current_app_version()
    os.makedirs(backup_dir, exist_ok=True)

    filename = f"{_STATEMENT_PREFIX}-{version}-{date_range}-{timestamp.strftime('%d%m%y-%H%M')}.xlsx"
    dest_path = os.path.join(backup_dir, filename)

    shutil.copy2(source_path, dest_path)
    _record_note(backup_dir, filename, note)
    return filename


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def export_database_to_sqlite(source_conn, dest_path: str) -> dict[str, int]:
    """V4.14 -- full copy of a live database (in practice Turso, via core/db.py's
    get_connection()) into a standalone local SQLite file. This replaces
    backup_database() as the way to protect real data: that function backs up
    data/portfolio.db, a frozen pre-Turso snapshot the running app no longer reads.

    Recreates every table from its own CREATE statement, copies every row, then
    recreates indexes/views/triggers AFTER the data load (a trigger created first
    would fire on the copied rows). SQLite-internal tables (sqlite_*) are skipped.
    The result is set to WAL journal mode -- Turso's "Upload SQLite File" restore
    path requires it (see docs/BACKUP_AND_TESTING.md).

    Read-only against the source: only SELECT/PRAGMA statements are issued. Raises
    FileExistsError rather than overwriting an existing file, and deletes a
    half-written file if anything fails, so a broken file can't be mistaken for a
    real backup. Returns {table name: rows copied}."""
    if os.path.exists(dest_path):
        raise FileExistsError(f"{dest_path!r} already exists -- refusing to overwrite a backup.")

    tables = [
        (name, sql) for name, sql in source_conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE type = 'table' AND sql IS NOT NULL ORDER BY name"
        ).fetchall()
        if not name.startswith("sqlite_")
    ]
    extras = [
        sql for (sql,) in source_conn.execute(
            "SELECT sql FROM sqlite_master WHERE type IN ('index', 'view', 'trigger') AND sql IS NOT NULL "
            "ORDER BY CASE type WHEN 'index' THEN 0 WHEN 'view' THEN 1 ELSE 2 END, name"
        ).fetchall()
    ]

    counts: dict[str, int] = {}
    dest = sqlite3.connect(dest_path)
    try:
        for name, create_sql in tables:
            dest.execute(create_sql)
            column_count = len(source_conn.execute(f"PRAGMA table_info({_quote_ident(name)})").fetchall())
            rows = source_conn.execute(f"SELECT * FROM {_quote_ident(name)}").fetchall()
            placeholders = ", ".join("?" for _ in range(column_count))
            dest.executemany(f"INSERT INTO {_quote_ident(name)} VALUES ({placeholders})", rows)
            counts[name] = len(rows)
        for extra_sql in extras:
            dest.execute(extra_sql)
        dest.commit()

        for name, expected in counts.items():
            actual = dest.execute(f"SELECT COUNT(*) FROM {_quote_ident(name)}").fetchone()[0]
            if actual != expected:
                raise RuntimeError(f"Table {name!r}: copied {expected} rows but the backup file holds {actual}.")
        dest.execute("PRAGMA journal_mode=WAL")
    except BaseException:
        dest.close()
        os.remove(dest_path)
        raise
    dest.close()
    return counts


def backup_turso_database(
    source_conn,
    backup_dir: str = DEFAULT_BACKUP_DIR,
    *,
    env_label: str,
    timestamp: datetime | None = None,
    version: str | None = None,
) -> tuple[str, dict[str, int]]:
    """Names the file bk-turso-<env>-<version>-<ddmmyy>-<hhmm>.db (env_label is
    'prod' or 'dev', so a dev copy can never be mistaken for real data) and runs
    export_database_to_sqlite() into backup_dir. Deliberately a different prefix
    from backup_database()'s bk-portfolio-*, so list_backups() and the (now
    hidden) System Backup page never pick these up. Returns (filename, counts)."""
    env_label = re.sub(r"[^A-Za-z0-9]+", "", str(env_label)) or "unknown"
    timestamp = timestamp or datetime.now()
    version = version if version is not None else current_app_version()
    os.makedirs(backup_dir, exist_ok=True)
    filename = f"bk-turso-{env_label}-{version}-{timestamp.strftime('%d%m%y-%H%M')}.db"
    counts = export_database_to_sqlite(source_conn, os.path.join(backup_dir, filename))
    return filename, counts


def backup_turso_database_bytes(
    source_conn=None,
    *,
    env_label: str,
    timestamp: datetime | None = None,
    version: str | None = None,
) -> tuple[str, bytes, dict[str, int]]:
    """In-memory variant of backup_turso_database() for the Dashboard's "Backup DB"
    button: builds the same file in a temp directory that is deleted straight away
    and returns (filename, file bytes, counts) for st.download_button. Nothing is
    left on the server's disk -- which matters on Streamlit Community Cloud, whose
    disk is ephemeral, so a saved file there would simply vanish. `source_conn`
    defaults to a fresh Turso connection (closed afterwards); pass one in to test."""
    should_close = source_conn is None
    if should_close:
        from core import db

        source_conn = db.get_connection()
    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            filename, counts = backup_turso_database(
                source_conn, tmp_dir, env_label=env_label, timestamp=timestamp, version=version,
            )
            with open(os.path.join(tmp_dir, filename), "rb") as f:
                data = f.read()
    finally:
        if should_close:
            source_conn.close()
    return filename, data, counts


def delete_backup(backup_dir: str, filename: str) -> None:
    """Deletes a single backup file and its manifest.json note, if any.
    Raises FileNotFoundError if the file doesn't exist -- deleting
    something that's already gone points at a stale UI/caller state, not
    something to silently ignore. Irreversible: there's no undo beyond
    whatever other backups still exist, so callers should confirm with the
    user before calling this (see app_pages/backup.py's delete popover)."""
    path = os.path.join(backup_dir, filename)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"{filename!r} not found in {backup_dir!r}")
    os.remove(path)

    manifest = _load_manifest(backup_dir)
    if filename in manifest:
        del manifest[filename]
        _save_manifest(backup_dir, manifest)


def _parse_backup_filename(filename: str):
    """Returns (type, version, created) parsed back out of a filename this
    module generated, or None if it doesn't match the expected shape at
    all (e.g. a stray file someone else dropped into the backups folder --
    skipped rather than raising, so one odd file can't break the whole
    history list)."""
    stem, ext = os.path.splitext(filename)
    if ext == ".db" and stem.startswith(_DB_PREFIX + "-"):
        kind, rest = "Database", stem[len(_DB_PREFIX) + 1:]
    elif ext == ".xlsx" and stem.startswith(_STATEMENT_PREFIX + "-"):
        kind, rest = "Statement", stem[len(_STATEMENT_PREFIX) + 1:]
    else:
        return None

    match = _TIMESTAMP_SUFFIX_RE.match(rest)
    if not match:
        return None
    rest, timestamp_str = match.groups()
    try:
        created = datetime.strptime(timestamp_str, "%d%m%y-%H%M")
    except ValueError:
        return None

    if kind == "Statement":
        date_match = _DATE_RANGE_RE.match(rest)
        if date_match:
            rest = date_match.group(1)
    version = rest

    return kind, version, created


def list_backups(backup_dir: str = DEFAULT_BACKUP_DIR) -> pd.DataFrame:
    """Filename, Type (Database/Statement), Version, Created, Size, Note --
    sorted newest first. Note comes from the manifest.json sidecar,
    defaulting to "" for a backup that was never given one. manifest.json
    itself, and any other stray file, is silently skipped (not an error --
    _parse_backup_filename() returns None for anything that doesn't match
    this module's own naming shape). Empty DataFrame (not an error) if
    backup_dir doesn't exist yet -- no backups taken is a normal, expected
    state."""
    columns = ["Filename", "Type", "Version", "Created", "Size", "Note"]
    if not os.path.isdir(backup_dir):
        return pd.DataFrame(columns=columns)

    manifest = _load_manifest(backup_dir)
    rows = []
    for filename in os.listdir(backup_dir):
        path = os.path.join(backup_dir, filename)
        if not os.path.isfile(path):
            continue
        parsed = _parse_backup_filename(filename)
        if parsed is None:
            continue
        kind, version, created = parsed
        rows.append({
            "Filename": filename, "Type": kind, "Version": version,
            "Created": created, "Size": os.path.getsize(path),
            "Note": manifest.get(filename, ""),
        })

    return pd.DataFrame(rows, columns=columns).sort_values("Created", ascending=False, ignore_index=True)
