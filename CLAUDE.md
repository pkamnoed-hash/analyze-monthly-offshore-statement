# Orientation for Claude Code

Notes specifically for a Claude Code session working in this repo -- not a
duplicate of the other docs, just pointers to them plus a couple of things
worth knowing before touching anything.

## Where things live

- `core/` -- all business logic (auth, calculations, db, slip_parser,
  reconciliation). No Streamlit imports anywhere in here, deliberately --
  keeps everything unit-testable. Read `docs/ARCHITECTURE.md` before
  adding a new module here.
- `app_pages/` -- Streamlit UI only (layout, widgets, session state).
  Thin -- logic belongs in `core/`, not here.
- `dashboard_app.py` -- the entry point (login gate + `st.navigation`
  router). Stays at the project root.
- `scripts/` -- one-off/seed scripts, run manually, not part of the app's
  request path. `seed_from_xlsx.py` is the one that matters most (one-time
  xlsx -> SQLite import). `scripts/reconcile.py` and
  `scripts/merge_into_workbook.py` were confirmed broken/one-off during V2
  research -- don't assume they work, and don't extend `core/reconciliation.py`
  to depend on them.
- `tests/` -- one file per `core/` module, pytest, no real API calls (slip
  parser tests inject a fake Anthropic client).
- `docs/` -- see `README.md`'s doc index for what's where.
- `labs/` -- **real PII** (account numbers, a real name, personal
  financial planning data). Gitignored. Fine to read locally for grounding
  (e.g. verifying a UI design decision against a real slip screenshot, as
  V1 and V2 both did), never commit or publish its contents anywhere.

## Before starting non-trivial work

- Check `docs/ROADMAP.md` first for what's already built and why --
  several past "obvious" changes turned out to already have a deliberate
  reason documented there (e.g. why dividends use Gross+Withholding
  instead of a flat 15%, why `entry_type` is never part of a reconciliation
  match key).
- Run the full test suite (`pytest -q`) before and after any change to
  `core/` -- it's fast (a few seconds) and every module is designed to be
  tested this way.

## If you use Plan Mode here

**Fold the finished plan into `docs/ROADMAP.md` once the feature ships.**
This is not optional advice -- it's a direct lesson from a real incident:
the V1 build plan lived only in the Claude Code plan file
(`~/.claude/plans/*.md`) and got silently overwritten by a later, unrelated
planning session before it was ever committed anywhere. It had to be
reconstructed from conversation memory. That plan file is a scratchpad, not
a durable store -- it survives only until Plan Mode is used for something
else. Once a feature is done and tested, port the plan's Context/Research/
Implementation/Verification content into `docs/ROADMAP.md` (see that
file's V1 and V2 sections for the expected shape) before moving on to the
next thing.

## Data safety

`data/portfolio.db` is real financial data, not a fixture. Prefer
`conn=`-injected in-memory SQLite for anything exploratory; if you must
touch the real file, back it up first (`cp data/portfolio.db
data/portfolio.db.bak`) -- there is no undo beyond that copy or manually
clearing whatever column you changed.
