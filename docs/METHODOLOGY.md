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
