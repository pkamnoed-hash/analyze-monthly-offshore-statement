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
4. Edit `.streamlit/secrets.toml`, replacing `TURSO_DATABASE_URL`/
   `TURSO_AUTH_TOKEN` with the `dev` branch's values, **and add/set
   `APP_ENV = "dev"`** (see "Which environment am I on?" below). Leave
   the other 3 keys (password/Anthropic) unchanged.
5. Restart `run_dashboard.bat` (kill any running instance first). Local
   testing now reads/writes only `dev` -- production is untouched no
   matter what happens.
6. When done: copy the values back from `secrets.prod.toml.bak` into
   `secrets.toml` (which should have `APP_ENV = "prod"`, or no `APP_ENV`
   key at all -- it defaults to prod) and restart again.

Tip: keep `secrets.prod.toml.bak` and a `secrets.dev.toml` side by side
permanently, and just copy whichever one you want active *into*
`secrets.toml` (the file Streamlit actually reads) when switching
contexts, rather than hand-editing values each time.

### Which environment am I on?

The sidebar shows a colored **"DEV environment"** (green) or **"PROD
environment"** (red) badge, right under the version label -- driven by an
`APP_ENV` key in `secrets.toml` (`"dev"` or `"prod"`; defaults to `"prod"`
if the key is missing, so older secrets files without it still show the
safe default). Rendered as a solid-color badge rather than emoji circles
-- an earlier version used 🟢/🟡, but they didn't render distinctly enough
to trust at a glance, which defeated the point. This is a deliberately
explicit, manually-set flag rather than something inferred from the Turso
URL -- toggle it in the same edit as `TURSO_DATABASE_URL`/
`TURSO_AUTH_TOKEN` above, every time you switch. Check this label before
doing anything on real data, especially after switching back and forth a
few times in one session.

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

## Practice lab: try it yourself

Reading how this works isn't the same as knowing you can actually do it
under pressure. These five scenarios were run for real once (not just
theorized) -- each has a concrete checkpoint proving it worked, and
scenarios 2-5 all build on the `dev` branch scenario 1 creates, keeping
the whole exercise off production data throughout.

**1. Create `dev` and confirm isolation.** Follow "Safe testing
environment" above to create `dev` and point your local app at it.
Checkpoint: log one obviously-fake trade (e.g. symbol `TESTXYZ`) in the
app, then check the Turso dashboard's **production** database (not
`dev`) and confirm it's *not* there.

**2. Practice a schema change.** Still on `dev`, add the
`_add_column_if_missing()` helper from "Schema changes" above to
`core/db.py`, call it once with a throwaway test column, and restart the
app. Checkpoint: confirm the column exists on `dev` (Turso's Edit Data
tab), then restart the app a *second* time with nothing changed and
confirm it doesn't error -- that's what proves the pattern is idempotent,
not just working once. Afterwards, revert the code change (`git checkout
-- core/db.py` if uncommitted) -- this was practice, not a real feature.
The column itself stays on `dev` harmlessly; deal with it in scenario 5.

**3. Practice a point-in-time rollback.** Note the time, make an
obviously-wrong change on `dev` (delete `TESTXYZ`, or mangle its price),
wait a minute or two, then in Turso: database's "..." menu -> **Create
Branch** -> **From Point-in-Time** -> pick a timestamp from before the
change -> name it `dev-restored`. (PITR in Turso's UI works this way --
creating a new branch from a past moment -- not an in-place restore
button.) Checkpoint: open `dev-restored`'s Edit Data and confirm
`TESTXYZ` is back.

**4. Practice manual export + restore.** On `dev`, use "Export Database"
/ "Download SQLite File" to save a local snapshot. Add a second,
different throwaway trade to `dev` afterward. Create a **new, separate**
Turso database (Create Database -> Upload SQLite File, not a branch) and
upload the snapshot into it. Checkpoint: the new database has the first
throwaway trade but not the second one added afterward -- proving the
export is a frozen point-in-time copy, not a live link. Delete this
scratch database once confirmed.

**5. Wrap up.** Restore `secrets.toml` from your `secrets.prod.toml.bak`
copy (or manually restore the `TURSO_*` values and set `APP_ENV =
"prod"`), restart the app, and confirm the sidebar shows the red PROD
badge with none of the throwaway test trades visible. Decide whether to
keep `dev` around (it's now diverged from production after all this
practice -- resync "From Now" before next real use) or delete it and
create a fresh one whenever it's actually needed.
