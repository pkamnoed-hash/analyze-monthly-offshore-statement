# Changelog

## System Backup (v2.3): manual, on-demand safety net for your live data

Adds a **System Backup** page (Tools nav) for the two files most at risk
from an accident -- `data/portfolio.db` and the official Statement
workbook -- pulled forward ahead of Rebalance/Reallocate Investment
(shifted to v2.4) at the user's own priority call, right after discarding
and restarting the Rebalance build once already this session.

- **`core/version.py`** (new): `current_app_version()` derives a version
  label from the current git branch (e.g. `v2.3-system-backup` ->
  `v2.3`), falling back to the nearest tag, then `"unknown"` -- never
  hand-maintained, so it can't go stale. Also shown at the bottom of the
  sidebar on every page.
- **`core/backup.py`** (new): `backup_database()` uses SQLite's own
  online-backup API (not a raw file copy) reading the live db read-only,
  guaranteeing a consistent snapshot; `backup_statement_file()` matches
  the official Statement xlsx by glob pattern (not a hardcoded filename),
  since that file is periodically replaced with a new date-range name.
  Both embed the version label and a timestamp in the resulting filename;
  an optional free-text note is recorded in a `manifest.json` sidecar,
  not the filename itself. `delete_backup()` removes a file and its note
  together.
- **`app_pages/backup.py`** (new page, Tools nav): current-status section,
  two backup buttons each with an optional note field, and a backup-
  history table (All/Database/Statement filter) where every row carries
  its own Delete-with-confirm popover -- the same pattern
  `record_trade.py` already uses for deleting a logged trade.
  `data/backups/` is `.gitignore`'d immediately.
- Verified against the real `data/portfolio.db` and Statement file
  throughout: a database backup reproduced identical row counts (902
  trades, 895 dividends) to the live db; the Statement backup byte-
  matched the source exactly. Full test suite: 189/189 passing (25 new
  across `tests/test_version.py` and `tests/test_backup.py`).

## Monitor Stocks (v2.2): live market data for every current holding

Adds a **Monitor Stocks** page (Overview nav, alongside Dashboard) showing
every currently-held symbol in one table, filterable by Category (from
v2.1), enriched with live data from `yfinance` -- this project's first
non-Claude external network call.

- **`core/market_data.py`** (new): `fetch_stock_profile()` batches
  Description, Sector/Industry, Beta, Quote Type, and a 90-*calendar*-day
  price history per symbol (explicit `start`/`end` dates -- yfinance's
  `period="90d"` shorthand actually returns 90 *trading* days, ~131
  calendar days, confirmed by direct comparison). ETF fallbacks for two
  equity-only concepts yfinance otherwise leaves blank: Sector/Industry
  falls back to `fundFamily`/`category`; Beta falls back to `beta3Year`
  (confirmed to exactly match finviz.com's own displayed ETF Beta).
  Dividend figures (`Dividend Per Year`, `Dividend Yield %`, `Dividend
  Frequency`) are computed from `Ticker.dividends`' actual payout history
  rather than yfinance's `dividendRate` field, which is blank/$0 for ETFs
  despite real payouts. A bad/unresolvable symbol gets a NaN row rather
  than aborting the whole batch.
- **`app_pages/monitor_stocks.py`** (new): merges live holdings, Category,
  and market data; a **Refresh now** button + **Last refreshed** timestamp
  control the 5-minute cache. Per-symbol columns include Total Market
  Value, Unrealized/Unrealized %, Expected Div per Year/Month (net of the
  15% Thai withholding tax, matching Dashboard's own Dividends KPI
  convention), and Div Return Contribution % (applies the user's own
  Σ(wi × ri) portfolio-return formula to dividends, weighted by each
  symbol's share within its own category). A **Category Summary** KPI-card
  row (Holdings, Total Cost, Total Market Value, Unrealized %, Total
  Div/Yr, Total Div/Mth, Expected Div Return %) covers All/Others/
  Dividend/Growth at a glance, with resolved-symbols-only aggregation so
  one unresolvable holding can't produce a nonsensical percentage. Two
  pie charts (by Symbol, by blended Sector/Asset Class). Column labels
  abbreviated with full names kept as hover tooltips once the table grew
  past 20 columns.
- Verified against all 52 real current holdings throughout: ETF
  Sector/Industry blank count dropped from 28 to 1 of 52, Beta blank from
  29 to 2; every derived figure cross-checked by independent
  reconstruction (e.g. `Quantity x Avg Cost == Cost Basis` for all 52
  rows). Full test suite: 164/164 passing (16 new in
  `tests/test_market_data.py`).

## Allocation Type (v2.1): classify each symbol as Dividend or Growth

Adds symbol-level tagging to the "Tools" nav section, matching how the user
already tracks two parallel portfolios (Dividend / Growth) in a personal
Excel workbook. Tagging only -- no target-%/rebalance math yet, that's a
separately-deferred future feature this lays the groundwork for.

- **`core/db.py`**: new `symbol_types` table (`symbol` PK, `allocation_type`
  CHECK-constrained to `'Dividend'`/`'Growth'` -- `'Others'` is deliberately
  never a stored value) + `set_symbol_type`/`clear_symbol_type`/
  `fetch_symbol_types`. The fetch starts from every distinct symbol in
  `trades` (not just symbols someone has tagged), left-joins the type table,
  and fills missing rows with `"Others"` -- so a symbol that's been fully
  bought and sold (44 of 96 real symbols today) still shows up, not just
  current holdings.
- **`app_pages/allocation_type.py`** (new page): a `st.data_editor` grid
  covering every traded symbol, with two ways to classify -- bulk
  checkbox-select + "Set N selected to Dividend/Growth/Others" buttons for
  fast batch work, or edit a row's dropdown directly and click "Save
  dropdown changes" for one-off tweaks. A type filter (All/Others/Dividend/
  Growth) and a "Showing N of M" row count help work through a large
  backlog.
- **`app_pages/record_trade.py`**: an inline `Allocation Type` selectbox
  appears only when the symbol being saved has never appeared in `trades`
  before -- not merely "still Others" -- so the one-time bulk catch-up is
  never re-triggered on a symbol's later trades. Optional, defaults to
  Others, save proceeds either way.
- **`app_pages/dashboard.py`**: By Symbol tab gets an `Allocation Type`
  column; a new Dividend/Growth/Others metric row (Market Value + % of
  holdings) sits under the existing "Current Allocation" pie chart.
- Verified against real data: 96 traded symbols, all correctly defaulting
  to Others before any tagging; after live use, 43 Dividend / 29 Growth /
  23 Others, with the Dashboard's value-weighted breakdown (88.2% / 11.0% /
  0.7%) closely tracking the real Excel's own 87.1%/12.9% actual split.

## Reconciliation (v2): verify live-logged activity against each new official statement

Adds a **Reconciliation** page (new "Tools" nav section) that closes the
loop v1 opened: once a new official xlsx statement arrives, everything
logged live through Record Trade/Record Dividend up to that point needs
checking against it, not just trusted. Uses the `reconciled_month` schema
column that's existed since v1's Step 2 but was never read or written
until now.

- **`core/reconciliation.py`**: pure matching logic, no Streamlit/DB
  dependency. Trades match on `(Trade Date, Symbol, Quantity, Price)`;
  dividends match on the xlsx's grouped gross+withholding sum; interest
  matches on `(Trade Date, Net Amt)` only, deliberately ignoring xlsx
  Symbol since the seed script always blanks it. A shared duplicate-safe
  pairing helper (`_pair_1to1`) stops same-key duplicates on one side from
  fanning out and over-matching the other side.
- **Three sections on the Reconciliation page**: "Ready to confirm"
  (matched, grouped by statement month, bulk or per-month "Mark
  reconciled"), "Needs review" (logged rows with no xlsx match -- fix is
  delete-and-re-enter, same correction pattern as everywhere else in this
  app), and "Official activity not yet logged" (xlsx rows never logged at
  all -- arguably the highest-value check, since nothing else in the app
  would ever surface this).
- **`core/db.py`**: `fetch_unreconciled_trades`/`_dividends` (candidates:
  `<= cutoff` and not yet reconciled), `mark_reconciled`/`_bulk` (one
  transaction per statement month, since a bulk "mark all" can span dozens
  of months on a first run).
- Verified against this account's real broker slips/receipts (`labs/`) --
  the matching definitions line up exactly with the actual Gross/Withholding
  dividend split, fee-netting formula, and whole-share/fractional-share
  trade-leg splitting already documented in v1.
- First real run against this account's history: 902/902 trades and
  895/895 dividend+interest rows matched cleanly, 0 rows needing review, 0
  gaps -- confirming the audited xlsx and the live-logged data have agreed
  the whole time.

## Unified Portfolio Web App (v1): record trades and dividends, don't just view them

Adds a write path on top of the previously read-only dashboard, so new
activity no longer needs re-typing into a separate Excel workbook between
official broker statements.

- **Login gate**: a single shared password (salted SHA-256, `auth.py`)
  guards every page, including the Dashboard -- Streamlit binds to
  `0.0.0.0` by default, so this was a real, current exposure, not a
  hypothetical.
- **Persistent storage**: `data/portfolio.db` (SQLite, `db.py`), seeded once
  from the existing audited xlsx (`scripts/seed_from_xlsx.py`) so FIFO
  lot-matching and dividend continuity work from day one.
- **Multi-page shell**: `dashboard_app.py` is now a thin `st.navigation`
  router; the dashboard body moved to `app_pages/dashboard.py`.
- **Dashboard blending**: KPIs blend the audited xlsx (up to the last
  official statement) with a live FIFO recompute of anything logged since --
  see `docs/METHODOLOGY.md` for the full FIFO-vs-average-cost writeup. A new
  "Since Last Statement" panel shows what's been logged and the Realized
  P/L it produced, per row.
- **Record Trade page**: Upload Slip (Claude vision API parses a Dime!
  trade confirmation screenshot into an editable confirm form) and Manual
  Entry, both writing to the same trade record. Shows the current FIFO
  position and a live estimated Realized P/L as a sell is typed, before
  it's saved. Blocks saving a sell that exceeds the current position behind
  an explicit confirmation checkbox. Symbol is a searchable dropdown sourced
  from trade history, with a free-text fallback for a genuinely new symbol.
  Delete (with a confirmation popover) for mistakes -- editing is delete +
  re-enter, not in-place.
- **Record Dividend page**: a bulk entry grid (several rows in one sitting)
  taking Gross Amount + Withholding Tax as the two separate numbers Dime!
  itself shows, computing Net automatically -- deliberately not an assumed
  flat 15%, since Capital Distribution rows have no withholding line in the
  real data. A Recent list (with the same delete-with-confirm pattern) and a
  Matrix view (symbols x months, full history, with row/column totals).
- **`requirements.txt`** added, pinning all direct dependencies including
  the new `anthropic` SDK.

## Dashboard KPI clarity pass

- **Total Fees tooltip**: clarifies that the figure is the Fees sheet *plus*
  Transactions' Commission column, since the Fees tab table only shows the
  former (a real point of confusion when cross-checking against the raw
  tables).
- **Split "Dividends + Interest" into two KPIs**: Dividends (net of the 15%
  Thai withholding tax) and Interest, each with their own tooltip. Interest
  is genuinely $0 for most months in this account's history, so combining it
  with Dividends obscured which one was actually driving the number.
- **Added "Avg. Monthly Dividend"**: Dividends ÷ number of months in the
  selected period, so it stays meaningful across any duration filter.
- **Reorganized the KPI grid into three tiers**, mirroring the source
  statements' own sectioning (Cash/Account Summary vs. Realized Gain/Loss vs.
  Income Summary):
  1. Headline outcomes: Portfolio Value, Net Deposits, Investment Gain/Loss, ROI
  2. Income: Dividends, Avg. Monthly Dividend, Interest
  3. Capital gains/losses & costs: Realized P/L, Unrealized P/L, Total Fees
- **THB reference values**: a sidebar "USD → THB rate" input (adjustable,
  default 33.0) drives a small, unemphasized gray "≈ ฿..." line under every
  dollar KPI. It's a single flat rate applied uniformly -- not historically
  accurate for older months, just a present-day rough reference.

## Prior session: full-history data quality audit

See commit `41002b8` for the underlying data fixes this dashboard reports on:
- Reclassified $22,448.92 in misfiled "Journal Entry(Cash)" rows (Income →
  Deposits & Withdrawals), fixing Net Deposits (tracked).
- Fixed `compute_realized_pl`'s same-day transaction ordering, which had
  overstated MSTY's realized loss by $131.45 (a 2025-12-08 reverse split vs.
  same-day buy conflict).
- Hardened `compute_realized_pl` against unclassified Entry Types (e.g. a
  rights-offering distribution), currently a $0-impact fix.
- Re-verified all 42 months (Jan 2023 - Jun 2026) against source PDFs and
  against the workbook's own Validation sheet: fully clean.
