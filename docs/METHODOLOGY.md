# Dashboard methodology

How each KPI on the Financial Summary Dashboard is calculated, and why. This
consolidates reasoning that otherwise only lives in `help=` tooltips and code
comments in `dashboard_app.py` / `calculations.py` -- kept here so it can be
read as one document instead of pieced together from the UI.

All figures below are for whatever date range is currently selected in the
"Duration" sidebar control, unless noted otherwise.

## ROI (period)

```
ROI (period) = Investment Gain/Loss ÷ Capital Base × 100

Capital Base = Starting Value + Net Deposits (during the period)
Starting Value = portfolio's Total Market Value as of the month *before*
                 the period began (0 if the period starts at account inception)
```

`Starting Value` deliberately does **not** use the statement's own
`Beginning Balance ($)` column -- per the workbook's own Validation sheet,
that column is just the prior month's `Ending Cash` carried over, which
ignores every invested holding. Using it would badly understate capital for
any period with substantial existing positions and little new cash added
(e.g. "Past 3 Months").

**Annualized ROI** compounds the period return to a 1-year-equivalent rate,
so periods of different lengths (a 3-month window vs. the full 3.5-year
history) can be compared on the same basis:

```
Annualized ROI = ((1 + ROI/100) ^ (365.25 / days_in_period) - 1) × 100
```

Implemented in `calculations.py::compute_roi`. Returns `None` for either
figure when there's no capital base to divide by, a non-positive period, or
a loss large enough that raising a negative base to a fractional power would
be undefined.

### Worked example (Jan 2023 -- Jun 2026, "All")

| Component | Value |
|---|---|
| Starting value (before account inception) | $0.00 |
| Net deposits during the period | $46,318.17 |
| **Capital Base** | **$46,318.17** |
| Realized P/L | -$3,312.08 |
| Unrealized P/L | -$829.85 |
| Dividends | $6,984.71 |
| Interest | $89.42 |
| **Investment Gain/Loss** | **$2,932.20** |

- **ROI** = 2,932.20 ÷ 46,318.17 × 100 = **6.33%** (total return over 1,277 days)
- **Annualized** = (1.0633)^(365.25/1277) − 1 = **1.77%/yr**

The annualized figure is much lower than the raw one here because the raw
6.33% was earned over 3.5 years, not one -- spreading it out shows the
effective yearly pace. When a selected period is almost exactly 365 days,
the two figures converge to the same number (nothing left to compound).

## Investment Gain/Loss

```
Investment Gain/Loss = Realized P/L + Unrealized P/L + Dividends + Interest
```

The recommended headline performance number -- built entirely from trade
prices and holding values, so it isn't affected by how the statement labels
various cash movements (see the reconciliation note below).

## Realized P/L (est.)

Computed by `calculations.py::compute_realized_pl` using an **average-cost**
method over the full transaction history (buys, sells, stock splits, ReOrg
CA events). This is an estimate: it will differ from the broker's official
Realized ST/LT figures, which use specific-lot identification.

Over the full history the gap is $655.36 (average-cost total: -$3,312.08 vs.
broker-printed ST+LT total: -$4,098.90 as of Jun 2026), concentrated almost
entirely in one event: KLIP's 2025-12 reverse stock split, where purchase
lots ranged from $12.16 to $33.53/share -- exactly the scenario where
average-cost and specific-lot methods diverge most. Not a bug; see git log
for the full trace.

Same-day transactions are ordered with corporate actions (Stock Split/ReOrg
CA) processed *before* regular trades on that date, since corporate actions
take effect before market open. Getting this backwards previously overstated
one position's (MSTY) realized loss by $131.45 -- fixed, but worth knowing
the ordering is deliberate if extending this function.

## Realized P/L since the last statement -- FIFO, not average-cost

Everything above (`compute_realized_pl`) is kept **unchanged** for the frozen,
audited xlsx history -- it's already reconciled, and moving those numbers
would undo that work. But trades entered live through **Record Trade**
(manual or slip upload) are tracked at full lot-level detail, so
`calculations.py::compute_fifo_realized_pl` uses **FIFO lot matching**
instead: each symbol keeps a queue of `[quantity, cost_per_share]` lots, and
a sell draws down the *oldest* lot(s) first rather than blending into one
running average. A sell spanning multiple lots sums the cost across them;
stock splits rescale every open lot proportionally; a ReOrg CA closes all
lots for that symbol.

This is why a sell can realize a **loss** even when the sale price is above
your average cost: FIFO doesn't sell "the average," it sells specific,
already-purchased shares -- and if those particular (oldest) shares cost more
than both the sale price and the blended average, the trade still loses
money. Confirmed on a real trade: selling 10 shares of a DCA'd position at
$25/share (above its $18.54 blended average) produced a **-$6.93** loss,
because FIFO drew from a 2024 lot costing $25.69/share.

`compute_current_positions` builds on the same lot-tracking loop (factored
out into a shared `_run_fifo` helper) to expose the *current* open position
per symbol -- quantity and quantity-weighted average cost -- shown on Record
Trade while entering a new trade, so a sell's likely FIFO outcome isn't a
surprise. `estimate_sell_realized_pl` simulates a sell against that same lot
book live, as Quantity/Price are typed, before the trade is even saved
(commission excluded from the estimate, since fee fields aren't known yet at
that point).

**Blending**: `blended_realized_pl`/`blended_dividends` split at a `cutoff`
date (the xlsx Summary sheet's last covered month) -- everything dated on or
before it keeps the audited average-cost/xlsx figures; everything after
switches to the live FIFO recompute. Portfolio Value, Unrealized P/L, and
Holdings are **not** blended (that would need a live price feed and cash
ledger, out of scope) -- they stay labeled "as of `<cutoff month>`", with a
separate **Since Last Statement** panel showing what's been logged since,
labeled per-row with the Realized P/L it actually produced.

## Unrealized P/L

Sum of the `Unrealized` column across the latest month's Holdings rows
(excluding `*Cash`).

## Dividends / Avg. Monthly Dividend / Interest

- **Dividends** is net of the 15% Thai (NRA) withholding tax -- it sums both
  the `Dividends` and `Div. Adj(NRA Withheld)` Income entry types. Only
  dividends (not interest) are attributed per-symbol in the By Symbol tab.
- **Avg. Monthly Dividend** = Dividends ÷ number of months in the selected
  period, so it stays meaningful across any duration.
- **Interest** is cash-sweep/margin interest. This account rarely earns any
  in real time -- most months show $0 except a one-time Dec 2024 year-end
  reallocation that retroactively caught up several prior months at once.

## Total Fees

```
Total Fees = Fees sheet total (REG/TAF/CAT/ADR, etc.) + Transactions' Commission column
```

The Fees tab table in the dashboard only shows the Fees-sheet component --
summing just that table will come up short of this KPI by however much was
paid in trade commissions (over the full history: -$1.26 Fees vs. $105.09
Commissions, for a combined -$106.35). Commissions are already folded into
Realized P/L's cost-basis math, so this figure is display-only and isn't
double-subtracted anywhere else.

## Net Deposits (tracked)

Sum of the Deposits & Withdrawals sheet's `Net Amt` column for the period,
split into deposits (positive) and withdrawals (negative). Every cash
movement type -- including `Journal Entry(Cash)` bank-transfer entries --
belongs in this sheet; historically some of these had been misfiled into the
Income sheet instead for Jan 2023 -- Sep 2024, which silently excluded
$22,448.92 from this figure until it was reclassified.

## THB reference values

Every dollar KPI shows a small, unemphasized "≈ ฿..." line, driven by the
sidebar's `USD → THB rate` input (default 33.0, adjustable). It's one flat
rate applied uniformly across the whole selected period -- not historically
accurate for older months, just a present-day rough reference.

## Reconciliation note (shown when the gap exceeds $1)

`(Portfolio Value − Net Deposits)` is a second, cruder way to estimate gain
that should roughly match Investment Gain/Loss. When it doesn't, the
dashboard explains the gap inline -- currently that's mostly the Realized
P/L average-cost estimate described above, plus minor rounding accumulated
across months. Investment Gain/Loss is the more reliable number either way.

## Recording a trade (Record Trade page)

Both entry paths (Upload Slip, parsed via the Claude vision API in
`slip_parser.py`; Manual Entry) funnel into the same `db.insert_trade()`,
so they can't drift out of sync. `quantity` is always entered as a positive
magnitude with a Side dropdown -- the sign conversion to the xlsx
Transactions convention (+buy, -sell) happens once, at insert time.

**Fee netting** (`db.compute_net_commission`), one formula for both sides:

```
commission = commission_fee + vat + reserved_fee (SEC+TAF, sell only) - fee_rebate (coupon, sell only)
```

Verified against a real sell slip: `1.04 + 0.00 + 0.03 - 1.04 = 0.03`, and
`689.98 (gross Stock Amount) - 0.03 = 689.95`, matching the slip's own
printed Total Credit exactly.

**Oversell confirmation**: if a sell's quantity exceeds the symbol's current
FIFO position (see above), the save is blocked behind an explicit checkbox
("I understand this sells more than my current holding"), keyed per
symbol+quantity so confirming one oversell never silently carries over to a
different one. This is a soft gate, not a hard block -- a legitimate reason
to oversell is that an earlier buy simply hasn't been logged yet.

## Recording a dividend (Record Dividend page)

Takes **Gross Amount** and **Withholding Tax** as two separate numbers --
matching the two separate line items Dime! itself shows for a dividend
(e.g. "Dividend HDV: 2.45 USD" and "Dividend Withholding Tax HDV: -0.36
USD") -- and computes `Net = round(Gross - Withholding, 2)` silently at save
time. This is **not** an assumed flat 15%: Capital Distribution rows have no
separate withholding line at all in the real data (confirmed against an
actual Dime! activity receipt), so entering Withholding Tax as 0 there
correctly leaves Net equal to Gross. The `round()` avoids binary
floating-point noise (`2.45 - 0.36 == 2.0900000000000003` before rounding)
leaking into stored data.

Symbol is required unless Entry Type is Interest -- matches the xlsx seed
convention, where Interest rows carry no symbol. The Symbol dropdown is
sourced from trade history (a dividend can't happen before the stock was
bought, so this covers virtually every real case) via
`st.column_config.SelectboxColumn`, which -- unlike Record Trade's Symbol
field -- has no free-text escape hatch for a symbol that hasn't been traded
yet in this app.

## Reconciliation (Tools page)

Once a new official statement replaces the "Since Last Statement" window
described above (the `cutoff`), everything logged live through Record
Trade/Record Dividend up to that new cutoff needs verifying against the
newly-audited xlsx, not just trusted at face value. The Reconciliation page
(`core/reconciliation.py`) does that matching and marks confirmed rows via
the `reconciled_month` column -- present in the schema since Step 2, unused
until this feature.

**Matching keys** (exact, no fuzzy tolerance beyond the rounding noted):

- **Trades**: `(Trade Date, Symbol, Quantity rounded 6dp, Price rounded
  4dp)`. The rounding exists only because Record Trade's number input can't
  accept more precision than that -- not fuzziness.
- **Dividends**: xlsx Income rows for the same `(Trade Date, Symbol)` are
  grouped and summed (the gross `Dividends` row + the negative `Div.
  Adj(NRA Withheld)` row) and compared, rounded 2dp, against the logged net
  amount. Entry Type wording is deliberately excluded from the key -- the
  xlsx and SQLite vocabularies never line up 1:1 (Capital Distribution has
  no xlsx equivalent and matches the same way a Dividend does).
- **Interest**: matched on `(Trade Date, Net Amt rounded 2dp)` only -- xlsx
  Symbol is deliberately ignored, since `scripts/seed_from_xlsx.py` always
  stores Interest rows with a blank symbol regardless of what the xlsx
  shows.

Same-day, same-symbol, same-price rows with *different* quantities are real
(a dollar-based buy order filled as a whole-share leg plus a
fractional-share leg) and match independently -- confirmed on real data,
e.g. VRIG 2026-06-29: 1.0 + 1.190429 shares, both legs at $25.07.

**Three outcomes per candidate**:

1. **Ready to confirm** -- matched; a "Mark as reconciled" action (per
   statement month, or all at once) stamps `reconciled_month`, after which
   the row is never re-checked.
2. **Needs review** -- a logged row with no xlsx counterpart, almost always
   a data-entry mistake. No in-place edit anywhere in this app -- fixed by
   deleting and re-entering correctly in Record Trade/Record Dividend.
3. **Official activity not yet logged** -- the reverse direction: an xlsx
   row with no SQLite counterpart *at all*, checked against the full
   trades/dividends tables rather than just unreconciled candidates, so an
   already-reconciled row is correctly recognized as covered rather than
   looking like a fresh gap. Arguably the highest-value check, since a
   never-logged entry has no other surface in this app that would ever
   mention it.

The matching definitions above were cross-checked against this account's
real Dime! slip/receipt screenshots (a buy confirmation, a sell
confirmation, and a dividend activity receipt) -- confirming the
Gross/Withholding split, the fee-netting formula, and the whole-share/
fractional-share leg pattern all match exactly what this feature expects,
not just an assumption about how the broker's data is shaped.

## Allocation Type (Tools page)

Classifies each symbol ever traded as **Dividend** or **Growth**, matching
how the user already tracks two parallel portfolios in a personal Excel
workbook (one sheet per type). **Others** is the default for anything not
yet actively sorted -- it is never itself a stored value (see
`docs/DATA_MODEL.md`'s `symbol_types` section); a symbol shows Others
purely because no row exists for it yet. This is **tagging only** -- one
classification per symbol, not per trade, with no target-%/rebalance math
attached (that's the separately-deferred Rebalance planner in
`docs/ROADMAP.md`).

**Two ways a symbol gets classified**:

1. **One-time bulk catch-up** -- the Allocation Type page itself
   (`app_pages/allocation_type.py`), a `st.data_editor` grid covering every
   symbol that's ever appeared in `trades` (via `db.fetch_symbol_types()`,
   so a fully sold-out symbol still shows up -- not just current
   holdings). Two independent input paths on the same grid: check several
   rows' `Select` boxes and click a "Set N selected to Dividend/Growth/
   Others" button (applies immediately), or edit a row's dropdown directly
   and click "Save dropdown changes" (diffs against current state, only
   writes what actually changed). A `st.radio` filter (All/Others/
   Dividend/Growth) plus a "Showing N of M" row count help work through a
   large backlog without losing track of progress.
2. **First-trade inline field** -- going forward, `app_pages/record_trade.py`
   shows an `Allocation Type` selectbox only when the symbol being saved
   has **never appeared in `trades` before** (checked against
   `known_symbols`, the same list that drives the Symbol autocomplete) --
   not "has no type yet," since an existing-but-still-Others symbol from
   the bulk catch-up must never be re-prompted on every future trade of
   the same symbol. Optional, defaults to Others, save proceeds either
   way; only calls `db.set_symbol_type()` if a real choice (Dividend/
   Growth) was made.

Once the one-time bulk pass is done, the inline field is self-sustaining --
there's no recurring maintenance task, only an occasional revisit via the
Tools page if you want to *change* an existing symbol's type.

**Dashboard integration**: the By Symbol tab's table gets an `Allocation
Type` column (via the same `fetch_symbol_types()` merge), and a
Dividend/Growth/Others `st.metric` row (Market Value + % of holdings) sits
under the existing "Current Allocation" pie chart, grouped the same way
that chart's data already supports, just by allocation type instead of by
symbol. A large Others total there is expected right after this feature
first ships -- it's the visible prompt to go classify more via the Tools
page, not a bug.

## Monitor Stocks (Overview page)

Live per-symbol table for current holdings only, via `core/market_data.py`
(`yfinance`, cached `@st.cache_data(ttl=300)`, "Refresh now" busts the
cache on demand). See `docs/ROADMAP.md`'s V2.2 section for the full build
history and real-data findings; this section is the calculation reference.

**Weight % vs. Category Weight %**: `Weight %` is each symbol's share of
the *entire* current portfolio's market value; `Category Weight %` is its
share of just its own Dividend/Growth/Others group's market value. Both
use `Quantity x Latest Price` (`Position Value`) as the underlying $ figure
-- not Cost Basis, since these describe current allocation, not money in.

**Unrealized / Unrealized %**: `Position Value - Cost Basis` and
`(Position Value - Cost Basis) / Cost Basis * 100`. Live and FIFO-based
(via `calculations.compute_current_positions()`), computed from the same
lot book as Record Trade's current-position display -- a different,
live number from Dashboard's own `Unrealized`, which stays pinned to the
last official statement's snapshot values. The % version returns NaN
(not a crash) when Cost Basis is 0 or negative.

**Dividend figures are net of the 15% Thai (NRA) withholding tax** --
`WITHHOLDING_TAX_RATE = 0.15` in `app_pages/monitor_stocks.py`, matching
Dashboard's own Dividends/Avg. Monthly Dividend KPIs (which are net
because the broker's recorded amount already is; here the deduction is
applied explicitly since yfinance's dividend yield is gross). `% Div per
Year` stays gross -- yields are conventionally quoted pre-tax; only the
dollar figures below are adjusted:

```
Expected Div per Year  = Total Market Value x (% Div per Year / 100) x 0.85
Expected Div per Month = Expected Div per Year / 12
```

Worked example (CLOZ, a real holding): 39.2122 shares x $26.4050 = $1,035.40
Total Market Value; $1.9280/share paid over the trailing 365 days = 7.30%
gross yield; $1,035.40 x 0.0730 x 0.85 = **$64.26**/year net, **$5.36**/month.

**Div Return Contribution %** applies the standard portfolio-return formula
(Portfolio Return = Σ(wi × ri), wi = asset value ÷ total value) to
dividends specifically, at the category level:

```
Div Return Contribution % = (Category Weight % / 100) x % Div per Year x 0.85
```

Summing this column across every symbol in one category reproduces that
category's blended dividend yield exactly -- proven algebraically (`wi` is
already normalized to sum to 100% within its own category) and confirmed
real: SHV (15.97% of the Dividend category, 3.78% yield) contributes
0.5127%; summed across all Dividend holdings, 8.3564%, matching a direct
`Total Div/Yr ÷ Total Market Value` calculation exactly. This equivalence
holds for any single category but **not** for summing across categories to
get a portfolio-wide figure (each category's own weights already sum to
its own 100%, so a raw cross-category sum would ignore relative category
sizes) -- the Category Summary's "Expected Div Return %" KPI is therefore
computed directly as `Total Div/Yr ÷ Total Market Value` for every row
including "All", not by summing the per-symbol column.

**Category Summary KPIs**: Total Cost sums every symbol in a category
(always known). Total Market Value, Unrealized, Unrealized %, Total Div/Yr,
and Expected Div Return % all sum **resolved symbols only** (symbols
yfinance could fetch a live price for) -- a category containing an
unresolved symbol shows a caption naming it and excluding it from those
figures, rather than a silently wrong number (a naive calculation that
divided a resolved-only $0 market value by an all-symbol cost once
produced a nonsensical -100% for a single unresolved options-contract
holding).

**Beta/Sector/Industry ETF fallbacks**: see `core/market_data.py`'s own
docstring -- yfinance's equity-oriented fields (`sector`/`industry`/`beta`)
are blank for most ETFs; `fundFamily`/`category`/`beta3Year` fill in as
fallbacks only when the equity-specific field is blank, so equities are
never affected.

## System Backup (Tools page)

Manual, on-demand backups only -- see `docs/ROADMAP.md`'s V2.3 section for
the full build history. This section is the format/logic reference.

**Filename format**: `bk-<type>-<version>-[<date range>-]<ddmmyy>-<hhmm>.<ext>`
- Database: `bk-portfolio-v2.3-290726-1430.db`
- Statement: `bk-statements-v2.3-2023-01_to_2026-06-290726-1430.xlsx` (the
  date range is copied verbatim from the source file's own name -- that
  range is the file's real identity, distinct from the app version)

**Version label** comes from `core/version.py`'s `current_app_version()`
-- the current git branch's leading `vN.M`/`vN` prefix (e.g.
`v2.3-system-backup` -> `v2.3`), falling back to the nearest tag for a
branch without a version prefix (e.g. `main`), falling back to `"unknown"`
if git itself is unavailable. Never hand-maintained, so it can't drift out
of sync the way a manually-bumped constant could -- also shown at the
bottom of the sidebar on every page.

**Database backups use `sqlite3.Connection.backup()`** (the stdlib's own
online-backup API), reading the source with a read-only connection
(`mode=ro`) -- guarantees a consistent snapshot even while the app has the
db open, and guarantees backing up can never itself write to the live db.
A plain `shutil.copy` can grab a torn, mid-write read if something else
has the file open; the backup API can't.

**Statement backups match `data/Offshore_Statements_*.xlsx` by glob**, not
a hardcoded filename -- this file is periodically replaced with a new
date-range name whenever a new month's official statement arrives.
Raises clearly (not a silent guess) if zero or more than one file matches.

**Notes** are stored in a `manifest.json` sidecar inside `data/backups/`
(`{filename: note}`), not embedded in the filename -- free text isn't safe
to fold into a filename that already carries type/version/date-range/
timestamp fields. A missing or corrupt manifest is treated as empty, never
an error.

## Rebalance & Reallocate (Tools page)

Manual, buy-only reallocation planner for Dividend-classified holdings
only -- see `docs/ROADMAP.md`'s V2.4 section for the full build history.
This section is the formula/logic reference.

**Universe**: Dividend-classified symbols currently held (`quantity >
0`), from `db.fetch_symbol_types()` filtered to `"Dividend"`. A symbol
tagged Dividend but fully sold out of simply doesn't appear.

**New-value recalculation**: buying `Invest $` = `amount x pct / 100`
more of a symbol at its current `Latest Price` adds `Invest $ / Latest
Price` shares. `New Cost Basis` = `Cost Basis + Invest $`; `New Value` =
`New Quantity x Latest Price`, which algebraically equals `Current Value
+ Invest $`. Since `New Unrealized $` = `New Value - New Cost Basis` =
`(Current Value + Invest $) - (Cost Basis + Invest $)` = `Current Value -
Cost Basis` = `Current Unrealized $`, it comes out **unchanged** -- buying
more at market price contributes zero unrealized gain/loss at the moment
of purchase. `New Unrealized %` still moves (down, for a gain position)
because the same $ gain/loss is now measured against a larger cost basis.

**Cat Weight %** is a symbol's share of the whole dividend basket's
value. Unlike Monitor Stocks' `Category Weight %` (which groups by
Dividend/Growth/Others across the whole portfolio), no groupby is needed
here -- every row is already Dividend, so it's a plain share-of-total.

**Div Contrib %** mirrors Monitor Stocks' `Div Return Contribution %`
exactly: `Cat Weight % / 100 x Dividend Yield % x (1 - 15% withholding)`.
Summed across every row, this reproduces the basket's blended annual
yield (`Total Expected Div/Yr / Total Value x 100`) -- the same algebraic
property that column already relies on, verified again here for the
dividend-only universe before building the page's "Blended dividend
yield" summary metric.

**Plan persistence** lives in two tables inside the same `portfolio.db`
(`rebalance_plans`, `rebalance_plan_items`), not a separate file --
automatically covered by the System Backup feature (V2.3) with no extra
work. Only one plan is ever active (`completed_at IS NULL`) at a time; it
auto-completes once every item is ticked Bought, or can be abandoned
early via "Reset plan."

**Edits are saved via `st.form`, not per keystroke**: `% Reinvest` and
`Bought?` are collected in the table and only written through when "Save
changes" is clicked, inside a real `st.form` -- Streamlit's own mechanism
for reliably flushing an in-progress, not-yet-committed cell edit
regardless of what widget triggered the save. A plain button *outside*
the grid was found, during testing, not to reliably capture an
in-progress edit that hadn't been explicitly committed first.
