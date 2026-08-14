# Changelog

## Monitor Stocks Reference Line Summary, Highlight Tab, Overall Rename (v4.4.1)

Portfolio-wide "nearest Reference Line" summary across all held
symbols, requested as a direct follow-up once v4.4's per-symbol
Reference Lines were working.

- **Monitor Stocks "Reference Lines" tab** -- Nearest Resistance/
  Support reading for every held symbol, computed on first load rather
  than requiring each symbol's own Auto Trendline page be visited
  first. Once current price reaches a captured level, that specific
  cell highlights amber and a new sortable "Passed R/S" date column
  records when.
- **"Highlight" tab** (moved to the leftmost position) -- consolidates
  the columns that matter most for "where do I need to pay attention"
  from across the other tabs: Ex-Date, Expt. Div/Mth, Total P/L(%),
  Div Yield %, and Nearest Resistance/Support.
  - **"Overview" renamed to "Overall"**, and its now-redundant Pivot
  Points columns (S3/S2/S1/Pivot/R1/R2/R3) dropped -- Nearest
  Resistance/Support cover the same "where's the watch-worthy level"
  question more directly; the full Pivot Points ladder stays on the
  dedicated Trendline tab.
- **Auto Trendline's own Zone 5 table** gets a matching "Passed R/S"
  column, reading the same underlying data Monitor Stocks does.
- Fixed a real pre-existing bug: opening a symbol's Auto Trendline
  page in a fresh session -- without editing anything -- silently
  wiped that symbol's whole "passed" state. Root cause was an unseeded
  guard on the database-hydration path; caught via live-data testing,
  not a synthetic test.
- New `captured_side`/`passed_at` columns on `reference_lines`, with a
  self-healing backfill for rows captured before v4.4.1.
- No breaking changes.
- Full test suite: 317/317 passing (18 new).

## Auto Trendline: Monitor Stocks Trendline tab + Reference Lines (v4.4)

Finally builds automatic support/resistance drawing, deferred since v4.2.

- **Monitor Stocks Trendline tab** -- classic Pivot Points (S3/S2/S1/
  Pivot/R1/R2/R3) for every current holding, anchored to your average
  cost rather than a market average, with amber highlighting when the
  latest price reaches a level. A "view" cell opens a new per-symbol
  chart page.
- **New "Auto Trendline" page** -- candlestick chart, MA 50/100/200,
  a Stochastic oscillator, Heikin Ashi, Day/Week/Month intervals, and
  1M-through-All timelines.
- **"Reference Lines"** -- the chart's support/resistance concept,
  consolidated from several earlier iterations into one: swing highs
  and lows nearest to your current price (not a fixed formula),
  captured at a moment you choose via a "Regenerate" button rather than
  constantly recomputing while you browse, fully editable (drag,
  delete, or add your own), and saved so a future notification feature
  can watch them without this page needing to be rebuilt.
- Two real bugs found in live testing, both fixed: dragging a line felt
  sluggish (a slow database write was blocking every redraw); clicking
  a line's remove button sometimes did nothing (the chart's own drag
  detection was intercepting the click first).
- No breaking changes.
- Full test suite: 299/299 passing (68 new).

## Rebalance & Reallocate: tab reorder, Ex-Date column (v4.3.1)

Two small follow-ups to v4.3, applied directly to `main` (no feature
branch) per explicit request.

- **Analyze moved to the first tab** (before Overview) -- it's the
  only editable tab, so it's now what you see first.
- **`Ex-Date`** added to Overview and Analyze -- same source and
  current-month amber highlight as Monitor Stocks' own Ex-Date column.
- No database/schema changes.
- Full test suite: 231/231 passing (no new tests -- display-only change).

## Rebalance & Reallocate overhaul, Monitor Stocks Monthly Dividend chart (v4.3)

Splits Rebalance & Reallocate's single wide table into 5 focused tabs,
adds real Total P/L tracking, and fixes a real Streamlit rendering bug
found along the way.

- **5 tabs** (Overview, Weight, Dividend Impact, Performance, Analyze)
  -- **Analyze** is the new sole editable tab (`% Reinvest`, `Bought?`,
  plus `Beta` and every allocation-impact column); Overview and the
  other 3 are read-only.
- **`Dividends Received` / `Total P/L` / `Total P/L %`** (Current +
  New) added, mirroring Monitor Stocks' own Total P/L formula exactly.
- **`Beta`** column added -- already-fetched data that was never
  surfaced on this page before.
- **Standalone THB -> USD reference calculator** above "$ amount to
  invest" -- not wired to the real input, just a quick conversion aid.
- **Summary KPI cards redesigned**: explicit Current/New side-by-side
  pairs instead of a value + delta badge.
- **Monitor Stocks**: added a Monthly Dividend bar chart below the two
  donut charts, scoped to the category filter. Validated line-by-line
  against a real broker statement PDF -- exact match.
- **Fixed a real Streamlit rendering bug**: two bare `$` in one
  caption/message get silently treated as inline math, mangling the
  text. Fixed across Rebalance, Dashboard, Record Trade, and Record
  Dividend.
- No database/schema changes.
- Full test suite: 231/231 passing (4 new).

## Monitor Stocks: Ex-Date column with current-month highlighting (v4.2)

Adds a real ex-dividend date to Monitor Stocks, after a "Payout Date"
companion column turned out to be unshippable.

- **`Ex-Date`** per symbol (Overview + Dividends tabs) -- the most
  recent ex-dividend date on record, sourced from `Ticker.dividends`'s
  own history. Works for every payer, including weekly/monthly funds.
- **Highlighted amber** when the ex-date falls in the current calendar
  month -- a quick "this cycle's window already passed" signal.
- Explored and removed a **Payout Date** column: `yfinance`'s
  `info["dividendDate"]` is blank for most funds and confirmed *stale*
  for at least one ETF (SHV returned a 2018 date for a fund paying
  monthly in 2026) -- not reliable enough to ship.
- No database/schema changes.
- Full test suite: 227/227 passing.

## Fix Monitor Stocks crash on Streamlit Community Cloud (v4.1.2)

Monitor Stocks crashed on the live deployed app (`AttributeError`, not
reproducible in local dev) right after v4.1 shipped -- the Holding
Period calculation's `.map()` call can infer a non-datetime dtype
depending on the pandas/platform build, which then breaks the `.dt.days`
call right after it.

- **`app_pages/monitor_stocks.py`**: the mapped holding-period-start
  values are now explicitly coerced with `pd.to_datetime(..., errors="coerce")`
  before the `.dt.days` calculation, guaranteeing a real datetime dtype
  regardless of environment-specific inference behavior.
- No database/schema changes.
- Full test suite: 225/225 passing.

## Monitor Stocks: Total Return, Holding Period, and tabbed columns (v4.1)

Adds real (not projected) performance tracking to Monitor Stocks, plus
splits its increasingly wide per-symbol table into focused tabs.

- **`Total P/L` / `Total P/L %`** per symbol -- Unrealized $ + actual
  Dividends Received (all-time), the first time this page has shown
  real historical dividend income rather than just a forward-looking
  projection.
- **`Holding Period (Years)` / `Total P/L %/yr`** per symbol -- how long
  you've continuously held your current position (resets if you fully
  exited and later rebought), and your annualized return over that
  period.
- **Category Summary** gets a third "Total Return" group (`Total P/L`,
  `Total P/L %`) alongside the existing "Holdings & Valuation" and
  "Dividend Projections" groups.
- **Per-symbol table split into 5 tabs** (Overview, Position,
  Performance, Dividends, Classification) instead of one 23-column
  table -- `Symbol` and `90D Trend` pinned in every tab.
- No database/schema changes.
- Full test suite: 225/225 passing (5 new, covering the holding-period
  calculation's edge cases).

## Fix oversell false-positive on a full-position sell (v4.1.1)

Selling your *entire* position in a symbol built from many small buys
(e.g. a DRIP-style holding) could incorrectly trigger "this would sell
more than you have," even when selling the exact amount shown as your
current position.

- **`app_pages/record_trade.py`**: the oversell check now tolerates the
  same tiny floating-point noise `core/calculations.py`'s
  `estimate_sell_realized_pl()` already tolerates (`+ 1e-9`) -- a
  position accumulated across many FIFO lots can land a hair below its
  displayed, rounded quantity (e.g. shown as `82.0812`, actually stored
  as `82.081199999998`), which tripped a strict `>` comparison.
- No database/schema changes -- pure comparison-logic fix.
- Full test suite: 220/220 passing.

## Dashboard & Monitor Stocks polish (v4): grouped KPIs, a live FX default, and a real bug fix

Refines how the Dashboard and Monitor Stocks pages present their
numbers, worked out iteratively against a mockup and live screenshots,
plus a real display bug found and fixed along the way.

- **Dashboard KPI cards** regrouped into three labeled sections instead
  of unlabeled 4+3+3 rows: Portfolio Overview (Portfolio Value, Net
  Deposits, Total Fees), Returns & Performance (Investment Gain/Loss,
  ROI, Realized P/L, Unrealized P/L), Income & Dividends (Dividends,
  Avg. Monthly Dividend, Interest).
- **Monitor Stocks' Category Summary** now shows one category at a time
  (matching the "Filter by type" radio) instead of All/Others/Dividend/
  Growth side by side, regrouped into two rows, and the page's
  explanation text moved into a collapsed "What do these numbers mean?"
  expander instead of a permanent paragraph under the title.
- **USD -> THB rate** now defaults to a live quote (via the same
  yfinance dependency already used for Monitor Stocks) instead of a
  hardcoded `33.0` -- still fully editable.
- **Fixed a real bug**: a freshly logged trade with nothing realized yet
  (a buy, no sells) showed the literal text "None" in the "Since Last
  Statement" table instead of a blank cell -- traced to
  `compute_realized_pl()`/`compute_fifo_realized_pl()` silently
  defaulting to `dtype=object` on an empty result.
- Removed a misleading green up-arrow next to a negative Unrealized %
  delta on Monitor Stocks (a Streamlit delta-sign-detection quirk).
- No database/schema changes.
- Full test suite: 220/220 passing.

## Testing environment (v3.1): know which environment you're on, and a safe way to test v4

Fixes the gap left right after going live: local dev could break with no
clear cause, there was no way to test risky changes (schema changes
especially) without touching real data, and no way to tell at a glance
whether you were looking at production or a test environment.

- **Sidebar environment badge**: shows "DEV environment" (green) or "PROD
  environment" (red), driven by a new `APP_ENV` secret (defaults to
  "prod"). Originally shipped as 🟢/🟡 emoji circles; replaced with a
  solid-color badge after you reported they weren't visually
  distinguishable.
- **`docs/BACKUP_AND_TESTING.md`** (new): how Turso's automatic backup
  (Point-in-Time Recovery) and manual export work, how to roll back, how
  to safely test using a Turso database branch instead of production, and
  the pattern for adding a column to an existing table safely (`init_db()`
  previously had no way to alter existing tables, only create new ones).
- **Practice lab**: five hands-on scenarios, each actually run once with
  a real, confirmed checkpoint -- dev-branch isolation, a schema-change
  rehearsal, a point-in-time rollback recovery, a manual export/restore
  round-trip, and restoring back to production.
- Root-caused the local dev crash along the way: a freshly-created Turso
  branch's connection endpoint had a brief propagation delay -- not a
  code bug, and not a general Turso reliability issue (confirmed Turso
  databases don't sleep or cold-start).
- No user-facing feature changes to the app itself -- this is entirely
  about safely developing what comes next.
- Full test suite: 217/217 passing.

## Hosting migration (v3): now live on the web, with data that actually survives a restart

The app is no longer local-only -- it's deployed to **Streamlit Community
Cloud** at `myinvestment27.streamlit.app`, backed by **Turso** (a free,
hosted, SQLite-compatible database) so new trades/dividends entered on
the live app persist across restarts and redeploys, not just locally.

- **`core/db.py`**: `get_connection()` now targets Turso instead of the
  local `data/portfolio.db` file, for both local dev and the deployed app
  alike -- one shared source of truth. Two internal rewrites made this
  safe without a live connection to test against ahead of time: schema
  creation now runs each `CREATE TABLE` individually instead of one
  multi-statement script, and a new `_read_sql()` helper replaces
  pandas' automatic SQLite/SQLAlchemy connection handling, which doesn't
  recognize Turso's connection type.
- Chose Streamlit Community Cloud + Turso after Hugging Face Spaces (the
  original pick) turned out to require a paid plan for the Docker SDK
  Streamlit needs, and after confirming Streamlit Community Cloud and
  Render's own free tiers both reset their disk on every redeploy --
  decoupling compute (Community Cloud) from storage (Turso) was the only
  combination that was both free and actually durable.
- `data/portfolio.db` is now a frozen pre-migration snapshot, not read by
  the running app -- kept as-is for System Backup and historical
  reference.
- No user-facing feature changes -- this is entirely about where the app
  runs and where your data lives, not what it does.
- Full test suite: 217/217 passing (unaffected -- tests always use an
  in-memory database, never the real connection).

## Rebalance & Reallocate Investment (v2.4): decide where new dividend cash goes

Adds a **Rebalance & Reallocate** page (Tools nav) for splitting new cash
across your Dividend-classified holdings -- rebuilt from scratch after an
earlier attempt was discarded, per your own explicit call, with a fresh
wireframe/flow worked out through direct discussion rather than reusing
any of the discarded design.

- **`core/rebalance.py`** (new): `get_dividend_holdings()` merges your
  Dividend-tagged, currently-held symbols with live price/dividend data
  and computes `Current Value`, `Current Cat Weight %`, `Current
  Unrealized $/%`, `Current Expected Div/Yr/Mo`, and `Current Div Contrib
  %`; `apply_allocation()` adds the `New-*` counterparts for a proposed $
  amount split across per-symbol %s, buying at each symbol's own live
  price; `sector_breakdown()` groups by sector/asset class.
- **`core/db.py` additions**: `rebalance_plans`/`rebalance_plan_items`
  tables (in the same `portfolio.db`, so System Backup already covers
  them) persist your in-progress plan across visits -- amount, per-stock
  %, and which rows you've ticked Bought. Auto-completes and clears once
  every row is ticked; a "Reset plan" button can abandon it early.
- **`app_pages/rebalance.py`** (new page, Tools nav): a Summary section
  (existing-vs-new pies for basket composition and sector mix), an
  Amount-to-invest input, `% allocated`/`% remaining` and KPI numbers
  (Expected Div/Mo, Unrealized %, blended dividend yield), and an
  editable per-stock table (`% Reinvest`, `Invest $`, `Div Contrib %`/`New
  Contrib %`, and a `Bought?` reminder checkbox) with a "Save changes"
  button.
- Two real bugs were found and fixed during your own live testing: the
  table's row position/scroll was resetting on every `%` edit (fixed by
  freezing the table's data until Save, instead of rebuilding it on every
  keystroke), and saving a `%` edit alone (without also touching
  `Bought?`) sometimes didn't take effect (fixed by wrapping the table
  and Save button in a real `st.form`, Streamlit's own mechanism for
  reliably capturing an in-progress, not-yet-committed edit).
- Small unrelated fix along the way: the login page now submits on Enter,
  not just on clicking "Log in."
- Verified against your real 26 dividend holdings throughout, including a
  real-data sanity check before any UI existed. Full test suite:
  217/217 passing (28 new across `tests/test_rebalance.py` and
  `tests/test_db.py`).

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
