# Backup, rollback, and safe testing (Turso)

Since the v3 hosting migration, the real data lives in **Turso**, not a
local file (see `docs/ARCHITECTURE.md`'s "Hosting" section and
`docs/DEPLOYMENT.md`). This document covers how backups already happen,
how to roll back, how to test changes without risking real data, and how
to change the schema safely.

## Backup

**Automatic, already happening, zero setup**: Turso backs up at every
single commit via **Point-in-Time Recovery (PITR)**. On the free plan you
can restore to any point within the **last 24 hours**. This has been
protecting the database since it was created -- nothing to configure.

**Manual, for anything beyond 24 hours or a personal archive**: from the
Turso dashboard, the database's "..." menu (or its Overview page) has an
**"Export Database" / "Download SQLite File"** option -- one click grabs
a real local `.db` snapshot. Doing this occasionally (e.g. monthly) gives
you a personal archive beyond what PITR covers.

**Caveat**: the app's own **System Backup** page
(`app_pages/backup.py`) still backs up the old local `data/portfolio.db`
file -- which the running app no longer reads, per the v3 migration. It
doesn't protect real data anymore; Turso's PITR and manual export (above)
are the actual safety net now.

## Rollback

- **Within the last 24h**: restore to a specific timestamp via Turso's
  PITR (dashboard or CLI).
- **Older, or a specific known-good snapshot**: re-upload a previously
  downloaded `.db` file the same way the database was originally seeded
  (Turso dashboard -> "Upload SQLite File"). Requires the file to be in
  `journal_mode=WAL` first -- see the "Data migration" note in
  `docs/ROADMAP.md`'s V3 section if this comes up again.

## Safe testing environment (Turso branching)

Building or testing anything risky (schema changes, experimental
features) should never touch production data directly. Turso's
**database branching** (free on every plan, instant -- copy-on-write, no
data duplication) makes this easy.

**Example scenario**: v4 adds a new column to `trades`. You don't want to
test that against your real holdings.

1. Turso dashboard -> database's "..." menu -> **Create Branch** ->
   **From Now** (not "From Point-in-Time" -- that's for restoring an
   *older* state, not creating a current sandbox). Name it `dev`.
2. Open the new `dev` branch's own Overview page -> copy its **Database
   URL** and generate an **auth token** for it, same process as the main
   database.
3. **Before changing anything locally**, save your current production
   secrets: copy `.streamlit/secrets.toml` to `.streamlit/secrets.prod.toml.bak`
   (still gitignored, safe).
4. Edit `.streamlit/secrets.toml`, replacing just `TURSO_DATABASE_URL`/
   `TURSO_AUTH_TOKEN` with the `dev` branch's values. Leave the other 3
   keys (password/Anthropic) unchanged.
5. Restart `run_dashboard.bat` (kill any running instance first). Local
   testing now reads/writes only `dev` -- production is untouched no
   matter what happens.
6. When done: copy the values back from `secrets.prod.toml.bak` into
   `secrets.toml` and restart again.

Tip: keep `secrets.prod.toml.bak` and a `secrets.dev.toml` side by side
permanently, and just copy whichever one you want active *into*
`secrets.toml` (the file Streamlit actually reads) when switching
contexts, rather than hand-editing values each time.

## Schema changes

**The gap today**: `core/db.py`'s `init_db()` only runs
`CREATE TABLE IF NOT EXISTS` statements -- naturally idempotent for
*new* tables (safe to run on every app startup, which it does). There's
no mechanism yet for altering an *existing* table (e.g. adding a column
to `trades`) -- SQLite has no `ADD COLUMN IF NOT EXISTS`, so running an
`ALTER TABLE` twice on a column that already exists throws an error.

**The pattern to use** when this is needed: a small helper, called
alongside the `SCHEMA_STATEMENTS` loop in `init_db()` so it's safe to run
on every startup, forever:

```python
def _add_column_if_missing(c, table, column, coltype):
    existing = [row[1] for row in c.execute(f"PRAGMA table_info({table})").fetchall()]
    if column not in existing:
        c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
```

**Process**:
1. Develop and test the migration against the `dev` branch (above), with
   real-data-shaped data, before touching production.
2. Right before deploying, take a manual production export (on top of
   PITR) -- schema changes are riskier than a plain data write, worth the
   extra snapshot.
3. Deploy normally (merge to `main`, push). `init_db()` applies the new
   column automatically the next time the production app starts -- no
   separate manual migration step.
4. Verify on the live app that the new column exists and existing rows
   are unaffected.

**What this pattern can't cleanly handle**: renaming or removing a
column, or changing a column's type/constraints. SQLite's `ALTER TABLE`
is limited -- those need the "create a new table with the right shape,
copy data across, drop the old one, rename" dance, which is meaningfully
riskier than adding a column. Plan for that explicitly if it comes up,
rather than assuming the simple pattern above covers it.
