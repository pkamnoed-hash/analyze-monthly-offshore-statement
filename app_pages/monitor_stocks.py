import os
from datetime import datetime

import pandas as pd
import plotly.express as px
import streamlit as st

from core import calculations, db, market_data

# Matches Dashboard's own "Dividends"/"Avg. Monthly Dividend" KPIs (app_pages/dashboard.py),
# which are net of the 15% Thai (NRA) withholding tax -- there, that's already baked into
# the broker's recorded Net Amt; here, yfinance's dividend data is gross, so the deduction
# is applied explicitly to keep this page's dollar figures consistent with Dashboard's.
WITHHOLDING_TAX_RATE = 0.15
# Shown as a hover tooltip everywhere a tax-adjusted dividend figure appears (both the
# per-symbol table's column headers and the Category Summary's KPI cards) -- a visible,
# permanent reminder of the deduction, not just a mention buried in the page-top caption.
DIVIDEND_TAX_HELP = f"Net of {WITHHOLDING_TAX_RATE:.0%} Thai (NRA) withholding tax."

# Same xlsx + blending Dashboard's own "Dividends" KPI uses (calculations.blended_dividends)
# -- needed here for Total P/L below, which is actual dividends received, not the
# Expected Div/Yr projection already on this page (a different, forward-looking figure).
DATA_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "Offshore_Statements_2023-01_to_2026-06.xlsx"
)
DIVIDEND_ENTRY_TYPES = ["Dividends", "Div. Adj(NRA Withheld)", "Dividend", "Capital Distribution"]


@st.cache_data(ttl=300)
def _cached_fetch_stock_profile(symbols: list[str]) -> tuple[pd.DataFrame, datetime]:
    """Cached wrapper around market_data.fetch_stock_profile -- the only network call on
    this page. core/market_data.py deliberately has no Streamlit import (see its own
    docstring), so caching is applied here at the page boundary instead. 5-minute TTL:
    prices go stale like a live read, but don't need Record-Trade-save-level immediacy.
    "Refresh now" below calls .clear() on this same function to bypass the TTL on demand.
    Returns the fetch timestamp alongside the data -- computed here, inside the cached
    function, so it's captured once at cache-miss time and reused for every cache-hit
    until the TTL expires or Refresh now clears it (not recomputed on every rerun)."""
    return market_data.fetch_stock_profile(symbols), datetime.now()


@st.cache_data(ttl=300)
def _cached_reference_line_summary(symbols: list[str], latest_prices: dict) -> pd.DataFrame:
    """v4.4.1 -- Monitor Stocks' Reference Lines summary tab. Builds the "Nearest
    Resistance (R %)"/"Nearest Support (S %)" cell for every symbol WITHOUT requiring its
    own Auto Trendline page be visited first: any symbol with no captured
    `reference_lines` row yet gets auto-captured here (real price-history fetch +
    calculations.compute_reference_lines + db.save_reference_lines, same YTD/Daily basis
    the per-symbol page seeds a never-visited symbol with) -- the whole point of this tab.

    Cached like _cached_fetch_stock_profile above (same 5-minute TTL, cleared by the same
    "Refresh now" button) -- a cold run can mean dozens of real price-history fetches, but
    once a symbol is captured this only costs a cheap DB read on every subsequent call
    (auto-capture skips anything already present), so the TTL mainly governs how often the
    "passed" check re-evaluates against a fresh price, not repeated capture work.
    `latest_prices` is a plain {symbol: price} dict rather than the profile DataFrame --
    st.cache_data hashes it directly, and since _cached_fetch_stock_profile is itself
    cached with the same TTL, this dict is stable (and this function cache-hits) across
    reruns within the same window, not just within one script pass.

    db.mark_reference_lines_passed() runs every call (cheap -- reuses `latest_prices`
    already fetched for the page, no extra network cost) so a line crossed since the last
    cache refresh gets its passed_at set before this tab's cells are built.

    Returns "_R Passed At"/"_S Passed At" alongside the visible columns -- hidden helper
    columns (see the render loop below) carrying each side's own passed_at, so the
    Nearest Resistance/Support cells can be highlighted individually rather than only
    the shared "Passed R/S" date column.

    Known, accepted edge case: a genuinely flat/ultra-low-volatility symbol (real example:
    SGOV/SHV, near-zero price movement) can have ZERO swing candidates on either side, so
    compute_reference_lines returns two empty lists and save_reference_lines's
    delete-then-insert-all inserts nothing -- that symbol never shows up in `captured`,
    so it's never seen as "already captured" and gets re-attempted (one more real
    fetch_price_history call) every cache cycle instead of being permanently skipped.
    Accepted rather than fixed with extra schema complexity (e.g. a sentinel row) -- in
    practice this is a couple of symbols out of a real ~52-symbol portfolio, re-fetched at
    most once per 5-minute TTL window, not on every rerun."""
    captured = db.fetch_reference_lines()
    captured_symbols = set(captured["Symbol"]) if not captured.empty else set()
    today = pd.Timestamp.today().normalize()
    cutoff = pd.Timestamp(year=today.year, month=1, day=1)  # YTD -- matches the per-symbol
                                                              # page's own default basis for
                                                              # a never-visited symbol.

    for symbol in symbols:
        if symbol in captured_symbols:
            continue
        latest_price = latest_prices.get(symbol)
        if latest_price is None or pd.isna(latest_price):
            continue  # unresolved symbol -- nothing to anchor a capture against
        daily_history = market_data.fetch_price_history(symbol, 1825)
        if daily_history.empty:
            continue
        resampled_full = calculations.resample_ohlc(daily_history, "D")
        bars_in_range = int((resampled_full["Date"] >= cutoff).sum())
        window = min(25, max(3, bars_in_range // 25))
        reflines = calculations.compute_reference_lines(
            resampled_full["Date"], resampled_full["High"], resampled_full["Low"],
            latest_price, window=window, search_from=cutoff,
        )
        lines_to_save = [{"price": p, "is_override": False} for p in reflines["resistance"] + reflines["support"]]
        db.save_reference_lines(
            symbol, lines_to_save, latest_price=latest_price,
            captured_timeline="YTD", captured_interval="Day",
        )

    db.mark_reference_lines_passed(latest_prices)

    captured = db.fetch_reference_lines()  # re-fetch: includes anything just captured/marked passed
    rows = []
    for symbol in symbols:
        latest_price = latest_prices.get(symbol)
        symbol_lines = captured[captured["Symbol"] == symbol] if not captured.empty else captured
        if latest_price is None or pd.isna(latest_price) or symbol_lines.empty:
            rows.append({
                "Symbol": symbol, "Nearest Resistance (R %)": "—", "Nearest Support (S %)": "—",
                "Passed R/S": pd.NaT, "_R Passed At": pd.NaT, "_S Passed At": pd.NaT,
            })
            continue
        line_dicts = [
            {"price": row["Price"], "passed_at": row["Passed At"]} for _, row in symbol_lines.iterrows()
        ]
        resistance_lines = [d for d, side in zip(line_dicts, symbol_lines["Captured Side"]) if side == "resistance"]
        support_lines = [d for d, side in zip(line_dicts, symbol_lines["Captured Side"]) if side == "support"]
        r_cell = calculations.nearest_reference_cell(resistance_lines, "resistance", latest_price)
        s_cell = calculations.nearest_reference_cell(support_lines, "support", latest_price)
        # A symbol could, in principle, have both its nearest R and nearest S passed at
        # once (price whipsawed through both since the last capture) -- "Passed R/S" is
        # one column, so this surfaces the MORE RECENT of the two dates, the fresher alert.
        passed_dates = [d for d in (r_cell["passed_at"], s_cell["passed_at"]) if d is not None]
        rows.append({
            "Symbol": symbol,
            "Nearest Resistance (R %)": r_cell["text"],
            "Nearest Support (S %)": s_cell["text"],
            "Passed R/S": max(passed_dates) if passed_dates else pd.NaT,
            # Hidden helper columns (never in a TAB_COLUMNS list, kept off-screen via
            # column_order=cols below) -- "Passed R/S" alone can't tell the highlighter
            # WHICH side passed, only the more recent date of whichever side(s) did. These
            # carry each side's own passed_at so _highlight_passed_nearest_reference can
            # light up exactly the cell that was actually reached.
            "_R Passed At": r_cell["passed_at"] if r_cell["passed_at"] is not None else pd.NaT,
            "_S Passed At": s_cell["passed_at"] if s_cell["passed_at"] is not None else pd.NaT,
        })
    return pd.DataFrame(rows)


@st.cache_data
def _blended_dividend_rows() -> pd.DataFrame:
    """Actual (not projected) dividend/distribution rows, all-time, blended xlsx history
    (<=cutoff) + live db (>cutoff) -- the exact same data source and blending Dashboard's
    own per-symbol "Dividends" column already uses, just not previously loaded on this
    page. Cached like Dashboard's own load_data() -- the xlsx is static at runtime, only
    db.fetch_dividends() (uncached) can add new rows. Row-level (not pre-summed) so both
    _dividends_received_by_symbol() and the Monthly Dividend chart below can slice it
    differently without a second xlsx load."""
    xls = pd.ExcelFile(DATA_FILE)
    summary = pd.read_excel(xls, "Summary")
    income = pd.read_excel(xls, "Income")
    summary["Month"] = pd.to_datetime(summary["Month"], format="%Y-%m")
    income["Month"] = pd.to_datetime(income["Month"], format="%Y-%m")
    income["Trade Date"] = pd.to_datetime(income["Trade Date"], format="%m/%d/%Y", errors="coerce")
    cutoff = summary["Month"].max() + pd.offsets.MonthEnd(0)

    blended_income = calculations.blended_dividends(income, db.fetch_dividends(), cutoff)
    return blended_income[blended_income["Entry Type"].isin(DIVIDEND_ENTRY_TYPES) & blended_income["Symbol"].notna()]


def _dividends_received_by_symbol() -> pd.Series:
    return _blended_dividend_rows().groupby("Symbol")["Net Amt"].sum()


st.title("Monitor Stocks")
# Collapsed by default -- was a full-width st.caption() paragraph right under the title
# (pushed all the actual data below the fold). Same pattern as Dashboard's "Since Last
# Statement" expander: keeps the page opening clean while keeping the term definitions
# discoverable right near where Category Weight %/Cost per Share/etc. first appear below,
# rather than requiring a scroll to the bottom of the page to find them.
with st.expander("What do these numbers mean?"):
    st.caption(
        "Live market data for every symbol you currently hold, filterable by Category "
        "(Dividend/Growth/Others). Weight % is each symbol's share of your whole portfolio; "
        "Category Weight % is its share of just its own Dividend/Growth/Others group. "
        "Cost per Share and Total Cost are computed live from your full trade history "
        "(FIFO lot accounting) -- a different, live number from Dashboard's snapshot-based "
        "Cost Price. Unrealized % follows that same live FIFO cost basis. Expected Div per Year/"
        "Month are Total Market Value x % Div per Year, net of the 15% Thai withholding tax "
        "(matching Dashboard's own Dividends KPI convention) -- the actual cash expected from "
        "your position size, not a gross per-share rate; % Div per Year itself stays gross, "
        "computed from actual trailing-12-month payouts (not yfinance's often-blank-for-ETFs "
        "dividendRate). Prices are cached for 5 minutes."
    )

db_trades = db.fetch_trades()
holdings = calculations.compute_current_positions(db_trades)

symbol_types = db.fetch_symbol_types()
holdings = holdings.merge(symbol_types, on="Symbol", how="left")
holdings["Allocation Type"] = holdings["Allocation Type"].fillna("Others")
holdings = holdings.rename(columns={"Allocation Type": "Category"})

if st.button("Refresh now", help="Bypass the 5-minute cache and re-fetch live prices."):
    _cached_fetch_stock_profile.clear()
    _cached_reference_line_summary.clear()

profile, last_refreshed = _cached_fetch_stock_profile(holdings["Symbol"].tolist())
st.caption(f"Last refreshed: {last_refreshed.strftime('%d/%m/%Y %H:%M')}")

holdings = holdings.merge(profile, on="Symbol", how="left")
# Sector/Industry are yfinance's own terms -- displayed here as "Portfolio Group" and
# "Asset Class" respectively, matching the prototype project's reference table exactly.
holdings = holdings.rename(columns={"Sector": "Portfolio Group", "Industry": "Asset Class"})

# Blended classification for the second pie chart below: a stock's most meaningful grouping
# is its Sector (Portfolio Group); an ETF's is its Industry/Category fallback (Asset Class) --
# Sector is largely meaningless for a fund that spans many sectors. Anything that isn't a
# confirmed equity (ETFs, and the rare unresolvable symbol with Quote Type = None) uses
# Asset Class.
holdings["Classification"] = holdings["Portfolio Group"].where(
    holdings["Quote Type"] == "EQUITY", holdings["Asset Class"]
)

# Pivot Points (S3/S2/S1/Pivot/R1/R2/R3) off the same 90-day High/Low window
# History90D already uses -- see calculations.compute_pivot_points's own docstring
# for the formula. "Pivot" is "Avg Cost" (Cost/Sh) passed straight through,
# not the classic (High+Low+Close)/3 average -- these levels answer "where
# does price sit relative to what I actually paid," a buy/sell decision aid,
# not the classic "where is price in its own recent range" reading. The
# column_config below relabels "Pivot" to "Cost/Sh" for display, since that's
# literally what it now holds -- the separate original Avg Cost column is
# deliberately left out of the Trendline tab (would just be a duplicate of
# this one). The cross-highlight further below still compares each level
# against Latest Price (the current market price is what's actually moving,
# not the static cost basis). "Action" is just a static visual label here --
# the actual navigation is cell-selection further below (st.dataframe's
# on_select="rerun" + selection_mode="single-cell", checking whether the
# selected cell's column is "Action" + st.switch_page), not a LinkColumn. A LinkColumn's
# href is a plain HTML anchor rendered inside the data grid's own component
# frame, so clicking it triggers a full browser navigation rather than
# Streamlit's in-session routing -- confirmed this breaks the app's auth
# gate two ways: (1) a hard navigation spins up a brand-new, unauthenticated
# session, and (2) even after logging back in, dashboard_app.py's auth
# check calls st.stop() BEFORE st.navigation() ever runs, so the router
# never learns about the deep-linked URL and falls back to the default page
# (Dashboard)
# instead of Symbol Analysis. st.switch_page(..., query_params=...) stays
# entirely within the current session, sidestepping both problems.
for level, values in calculations.compute_pivot_points(
    holdings["High90D"], holdings["Low90D"], holdings["Avg Cost"]
).items():
    holdings[level] = values
holdings["Action"] = "view →"

# v4.4.1 -- Reference Lines summary (Nearest Resistance (R %)/Nearest Support (S %)),
# genuinely different from the Pivot Points columns above: swing-based, nearest to
# current price rather than a fixed formula, and CAPTURED (persisted, frozen until the
# symbol's own Auto Trendline page saves a change) rather than recomputed every render.
# Auto-captures any symbol with nothing saved yet -- see _cached_reference_line_summary's
# own docstring -- so every held symbol is watchable from this table without needing to
# visit each one's own page first.
latest_price_map = dict(zip(holdings["Symbol"], holdings["Latest Price"]))
reference_line_summary = _cached_reference_line_summary(holdings["Symbol"].tolist(), latest_price_map)
holdings = holdings.merge(reference_line_summary, on="Symbol", how="left")

holdings["Position Value"] = holdings["Quantity"] * holdings["Latest Price"]
total_value = holdings["Position Value"].sum()
holdings["Weight %"] = (holdings["Position Value"] / total_value * 100) if total_value else 0.0

# Each symbol's share of just its own Category's total, not the whole portfolio -- "Weight %"
# above already answers "how big is this in my whole portfolio"; this answers "how big is
# this within just my Dividend/Growth/Others book," e.g. for rebalancing within a group.
category_totals = holdings.groupby("Category")["Position Value"].transform("sum")
holdings["Category Weight %"] = (holdings["Position Value"] / category_totals * 100).where(category_totals > 0, 0.0)

# Portfolio Return = Sigma(wi x ri) applied to dividends, per the user's reference formula:
# wi = Category Weight % (this stock's share within its own category), ri = Dividend Yield %
# (gross). Net of the 15% withholding tax, consistent with every other "Expected..." dividend
# figure on this page. Summing this column within one category reproduces that category's
# Total Div/Yr (net) / Total Market Value x 100 exactly (proven algebraically -- wi is already
# normalized to 100% within its own category) -- confirmed real for Dividend: 8.3564% both ways.
holdings["Div Return Contribution %"] = (
    holdings["Category Weight %"] / 100 * holdings["Dividend Yield %"] * (1 - WITHHOLDING_TAX_RATE)
)

# Live FIFO cost basis vs. current market value -- same shape as Dashboard's "Unrealized"/
# "Unrealized %" but computed from this page's live inputs (Cost Basis, Position Value),
# not the snapshot. Unrealized $ has no division, so it's valid even for a $0-cost position
# (e.g. free shares from a rights offering) -- unlike Unrealized %, it isn't guarded behind
# Cost Basis > 0; it's simply NaN whenever Position Value itself is NaN (unresolved symbol).
holdings["Unrealized"] = holdings["Position Value"] - holdings["Cost Basis"]
holdings["Unrealized %"] = (
    (holdings["Position Value"] - holdings["Cost Basis"]) / holdings["Cost Basis"] * 100
).where(holdings["Cost Basis"] > 0, float("nan"))

# Total P/L = Unrealized $ + actual Dividends Received (all-time) -- same name Dashboard's
# By Symbol tab already uses for this concept (Realized P/L + Unrealized + Dividends), but
# this page only tracks currently-held positions (a fully-exited symbol doesn't appear here
# at all, same convention compute_current_positions() uses everywhere), so there's no
# Realized P/L term to add -- just Unrealized + Dividends for whatever's still held today.
holdings["Dividends Received"] = holdings["Symbol"].map(_dividends_received_by_symbol()).fillna(0.0)
holdings["Total P/L"] = holdings["Unrealized"] + holdings["Dividends Received"]
holdings["Total P/L %"] = (
    holdings["Total P/L"] / holdings["Cost Basis"] * 100
).where(holdings["Cost Basis"] > 0, float("nan"))

# Holding Period: years since the current position was last built from zero (a symbol
# fully sold and later rebought resets to the rebuy date -- see
# calculations.compute_holding_period_start's own docstring). Total P/L %/yr annualizes
# Total P/L % by this -- a lump total return isn't comparable across symbols held for very
# different lengths of time; the annualized rate is.
holding_start = holdings["Symbol"].map(calculations.compute_holding_period_start(db_trades))
# pd.to_datetime(...) forces a real datetime64 dtype regardless of what .map() inferred --
# confirmed on real data that when NO symbol in `holdings` has a match (e.g. every current
# holding was somehow absent from the start-date map), .map() falls back to dtype=object
# instead of datetime64, and (today - holding_start).dt.days then raises AttributeError:
# "Can only use .dt accessor with datetimelike values". Not reproducible on every
# platform/pandas build (seen on Streamlit Community Cloud's Linux/Python 3.14 environment,
# not this local Windows/Python 3.12 venv) -- coercing explicitly makes it deterministic
# either way instead of depending on .map()'s own dtype-inference behavior.
holding_start = pd.to_datetime(holding_start, errors="coerce")
holdings["Holding Period (Years)"] = (pd.Timestamp.today().normalize() - holding_start).dt.days / 365.25
holdings["Total P/L %/yr"] = (
    holdings["Total P/L %"] / holdings["Holding Period (Years)"]
).where(holdings["Holding Period (Years)"] > 0, float("nan"))

# Holding-based dollar income, not the per-share "Dividend Per Year" rate from
# core/market_data.py -- Total Market Value x (Dividend Yield % / 100). Confirmed
# algebraically and numerically identical to Outstanding Shares x Div per Share/Year
# (both reduce to the same product), but this path reuses columns already on the page
# instead of needing the raw per-share rate carried separately. NaN propagates correctly
# for an unresolved symbol since Position Value/Dividend Yield % are both already NaN there.
# Net of the 15% withholding tax (see WITHHOLDING_TAX_RATE above) -- % Div per Year itself
# stays gross (a fund's advertised yield is conventionally quoted pre-tax), only the dollar
# income figures are adjusted, matching Dashboard's own net-dollar-KPI convention.
holdings["Expected Div Per Year"] = (
    holdings["Position Value"] * (holdings["Dividend Yield %"] / 100) * (1 - WITHHOLDING_TAX_RATE)
)
holdings["Expected Div Per Month"] = holdings["Expected Div Per Year"] / 12


def _category_summary(holdings_df: pd.DataFrame) -> pd.DataFrame:
    """One row per Category (plus "All"). Total Cost sums every symbol -- Cost Basis is
    always known, sourced from compute_current_positions()'s live trade history, never
    from yfinance. Total Market Value/Unrealized/Unrealized %/Total Div per Year sum
    resolved symbols only (Position Value notna()) -- pairing Unrealized/Unrealized %'s
    numerator AND denominator from that same resolved-only subset (not against the
    all-symbol Total Cost) is what avoids a misleading figure when a category has an
    unresolved symbol -- naively dividing a resolved-only $0 market value by an
    all-symbol Total Cost produced a nonsensical -100% for a single unresolved
    options-contract holding. Unrealized here is exactly the sum of the per-symbol
    "Unrealized" column (Position Value - Cost Basis) over resolved rows."""
    rows = []
    for category in ["All", "Others", "Dividend", "Growth"]:
        sub = holdings_df if category == "All" else holdings_df[holdings_df["Category"] == category]
        resolved = sub[sub["Position Value"].notna()]

        total_cost = sub["Cost Basis"].sum()
        resolved_cost = resolved["Cost Basis"].sum()
        total_market_value = resolved["Position Value"].sum()
        unrealized = total_market_value - resolved_cost if resolved_cost > 0 else float("nan")
        unrealized_pct = (unrealized / resolved_cost * 100) if resolved_cost > 0 else float("nan")
        total_dividend = resolved["Expected Div Per Year"].sum()

        # Total P/L: same "sum over resolved rows" pattern as Unrealized above -- Total P/L
        # is Unrealized + Dividends Received per symbol, so it needs the same resolved-only
        # scoping to avoid an unresolved symbol's NaN Position Value silently dropping it.
        total_pl = resolved["Total P/L"].sum()
        total_pl_pct = (total_pl / resolved_cost * 100) if resolved_cost > 0 else float("nan")

        rows.append({
            "Category": category,
            "Holdings": len(sub),
            "Total Cost": total_cost,
            "Total Market Value": total_market_value,
            "Unrealized": unrealized,
            "Unrealized %": unrealized_pct,
            "Total Div/Yr": total_dividend,
            "Total P/L": total_pl,
            "Total P/L %": total_pl_pct,
        })

    summary = pd.DataFrame(rows)
    portfolio_value = summary.loc[summary["Category"] == "All", "Total Market Value"].iloc[0]
    summary["% of Portfolio"] = (summary["Total Market Value"] / portfolio_value * 100) if portfolio_value else 0.0
    summary["Total Div/Mth"] = summary["Total Div/Yr"] / 12
    # Sigma(wi x ri), computed directly as Total Div/Yr (already net) / Total Market Value --
    # NOT by summing the per-symbol "Div Return Contribution %" column, since that column's
    # wi is category-relative (normalized to 100% within its OWN category) and would give a
    # meaningless number if summed across categories for the "All" row. This direct formula
    # is mathematically identical to Sigma(wi x ri) for any single category (proven -- both
    # reduce to the same ratio) and also correct for "All", sidestepping that trap entirely.
    summary["Expected Div Return %"] = (
        summary["Total Div/Yr"] / summary["Total Market Value"] * 100
    ).where(summary["Total Market Value"] > 0, float("nan"))
    return summary


# Moved above Category Summary (was below it) -- type_filter now also gates which single
# category's KPIs render below, not just the pie charts/table further down the page.
type_filter = st.radio(
    "Filter by type", ["All", "Others", "Dividend", "Growth"], horizontal=True, label_visibility="collapsed",
)

view = holdings.copy()
if type_filter != "All":
    view = view[view["Category"] == type_filter]

st.subheader(f"Category Summary — {type_filter}")
category_summary = _category_summary(holdings)
row = category_summary[category_summary["Category"] == type_filter].iloc[0]

st.markdown("**Holdings & Valuation**")
v1, v2, v3, v4 = st.columns(4)
v1.metric("Holdings", int(row["Holdings"]))
v2.metric("Total Cost", f"${row['Total Cost']:,.2f}")
v3.metric(
    "Total Market Value", f"${row['Total Market Value']:,.2f}",
    delta=f"{row['% of Portfolio']:.1f}% of portfolio", delta_color="off",
)
if pd.isna(row["Unrealized %"]):
    v4.metric("Unrealized %", "N/A")
else:
    v4.metric(
        "Unrealized %", f"{row['Unrealized %']:.2f}%",
        # delta_color="off" -- this is an informational $ amount, not a change from a prior
        # value, so it shouldn't carry a directional arrow. Also sidesteps a Streamlit quirk
        # where a delta string like "$-1,113.09" (minus sign after the $) isn't recognized as
        # negative, so the default "normal" mode rendered a misleading green up arrow on a loss.
        delta=f"${row['Unrealized']:,.2f}", delta_color="off",
    )
st.divider()

st.markdown("**Dividend Projections**")
d1, d2, d3 = st.columns(3)
d1.metric("Total Div/Yr", f"${row['Total Div/Yr']:,.2f}", help=DIVIDEND_TAX_HELP)
d2.metric("Total Div/Mth", f"${row['Total Div/Mth']:,.2f}", help=DIVIDEND_TAX_HELP)
if pd.isna(row["Expected Div Return %"]):
    d3.metric("Expected Div Return %", "N/A")
else:
    d3.metric(
        "Expected Div Return %", f"{row['Expected Div Return %']:.2f}%",
        help=DIVIDEND_TAX_HELP,
    )
st.divider()

# Actual (not projected) total return -- distinct from both groups above: not a current
# snapshot (Holdings & Valuation) and not a forward-looking projection (Dividend
# Projections), so it gets its own group rather than being squeezed into either.
st.markdown("**Total Return**")
t1, t2 = st.columns(2)
t1.metric(
    "Total P/L", f"${row['Total P/L']:,.2f}",
    help="Unrealized + Dividends Received (actual, all-time), summed across this category's holdings.",
)
if pd.isna(row["Total P/L %"]):
    t2.metric("Total P/L %", "N/A")
else:
    t2.metric("Total P/L %", f"{row['Total P/L %']:.2f}%", help="Total P/L as a % of Total Cost.")

unresolved_symbols = view[view["Position Value"].isna()]["Symbol"].tolist()
if unresolved_symbols:
    st.caption(
        f"{len(unresolved_symbols)} symbol(s) unresolved ({', '.join(unresolved_symbols)}) -- excluded "
        "from Total Market Value, Unrealized %, and Total Div/Yr above; Total Cost still includes them."
    )

def _grouped_pie(source_df, group_col, title, top_n=10):
    """Top N groups by summed Weight % + a grouped "Other" slice -- otherwise up to 52
    individual symbols (the "All" view) or 19 distinct sector/fund-category groups render
    as an unreadable ring of slivers. Groups first (so a group with several small symbols
    still competes fairly against a group with one large one), then takes the top N groups."""
    grouped = source_df.groupby(group_col, dropna=True)["Weight %"].sum().sort_values(ascending=False)
    top = grouped.head(top_n)
    rest = grouped.iloc[top_n:].sum()
    plot_df = top.reset_index()
    if rest > 0:
        other_row = pd.DataFrame([{group_col: f"Other ({len(grouped) - top_n})", "Weight %": rest}])
        plot_df = pd.concat([plot_df, other_row], ignore_index=True)

    fig = px.pie(plot_df, names=group_col, values="Weight %", title=title, hole=0.4)
    fig.update_traces(textposition="inside", textinfo="percent+label")
    return fig


if not view.empty:
    pie_col1, pie_col2 = st.columns(2)
    with pie_col1:
        # Grouped by the blended Classification (Sector for stocks, Asset Class for ETFs) --
        # several symbols can share one group, so this is where the group-by-sum matters.
        class_pie = _grouped_pie(view, "Classification", f"Weight % by Sector / Asset Class -- {type_filter}")
        st.plotly_chart(class_pie, use_container_width=True)
    with pie_col2:
        # Grouped by individual Symbol -- top_n=10 here means "top 10 symbols", one row each,
        # so no separate group-by-sum step is needed (each symbol already has one Weight % value).
        symbol_pie = _grouped_pie(view, "Symbol", f"Weight % by Symbol -- {type_filter}")
        st.plotly_chart(symbol_pie, use_container_width=True)

    # Same chart as Dashboard's own "Monthly Dividend" (blended xlsx + live db, same
    # DIVIDEND_ENTRY_TYPES vocabulary), scoped here to the current type_filter's symbols
    # instead of Dashboard's date range -- this page has no date picker, only a category
    # one. All-time, not bounded to any window, matching how Dividends Received/Total P/L
    # above are already all-time on this page.
    div_rows = _blended_dividend_rows()
    div_rows = div_rows[div_rows["Symbol"].isin(view["Symbol"])]
    if not div_rows.empty:
        dividend_by_month = div_rows.groupby(div_rows["Trade Date"].dt.to_period("M"))["Net Amt"].sum().reset_index()
        dividend_by_month = dividend_by_month.rename(columns={"Trade Date": "Month"})
        dividend_by_month["Month"] = dividend_by_month["Month"].dt.to_timestamp()
        avg_monthly_dividend = dividend_by_month["Net Amt"].mean()

        fig_div = px.bar(
            dividend_by_month, x="Month", y="Net Amt",
            title=f"Monthly Dividend -- {type_filter} (net of 15% withholding tax)",
            color_discrete_sequence=["#1f77b4"],
        )
        fig_div.add_hline(y=avg_monthly_dividend, line_dash="dot", line_color="gray",
                           annotation_text=f"Avg: ${avg_monthly_dividend:,.2f}/mo", annotation_position="top left")
        fig_div.update_layout(yaxis_title="USD", xaxis_title="Month")
        st.plotly_chart(fig_div, use_container_width=True)

st.caption(
    f"Showing {len(view)} of {len(holdings)} symbols. On Overall/Trendline/Reference Lines/Highlight, "
    "click a row's Action cell (\"view →\") to open that symbol's price chart with its Support/"
    "Resistance levels drawn."
)

# Split from one 23-column table into focused tabs (Finviz-style column presets) --
# Symbol + History90D pinned in every tab so a row is always identifiable regardless
# of which view you're on. Each tab reuses the same underlying `view` dataframe and
# `column_config` below, just a different column subset. Overall shows nearly every
# column (the original unified table, renamed from "Overview" in v4.4.1 Improvement 3);
# Pivot Points (S3/S2/S1/Pivot/R1/R2/R3) are deliberately left off it as of the tweak
# below -- Nearest Resistance/Support already cover "where's the watch-worthy level"
# more directly, and the dedicated Trendline tab still has the full Pivot Points ladder
# for anyone who wants it. The rest are focused slices of the same data.
#
# "Highlight" is listed first (dict order == tab order, since st.tabs() reads
# TAB_COLUMNS.keys() directly below) -- moved to the leftmost position as the
# most-used "where do I need to pay attention" view.
TAB_COLUMNS = {
    # v4.4.1, Improvement 2 -- pulls the columns that matter most for "where do I need to
    # pay attention" together from across the other tabs into one consolidated view,
    # rather than checking each focused tab separately. Every column here already exists
    # elsewhere on this page; no new computation. "Passed R/S" is included (not in the
    # original ask) so it's shown as a real sortable date alongside the two cells whose
    # highlight (now living directly on Nearest Resistance/Support, not this column) it
    # explains. Action is included for the same reason every other data-rich tab has it --
    # an attention-worthy row with no way to jump to that symbol's own chart would be a
    # real usability gap.
    "Highlight": ["Symbol", "History90D", "Ex-Date", "Expected Div Per Month", "Total P/L",
                  "Total P/L %", "Dividend Yield %", "Nearest Resistance (R %)",
                  "Nearest Support (S %)", "Passed R/S", "Action"],
    "Overall": ["Symbol", "History90D", "Description", "Category", "Asset Class", "Portfolio Group", "Weight %",
                "Category Weight %", "Quantity", "Avg Cost", "Latest Price", "Cost Basis", "Position Value",
                "Unrealized", "Unrealized %", "Dividends Received", "Total P/L", "Total P/L %",
                "Holding Period (Years)", "Total P/L %/yr", "Dividend Yield %", "Dividend Frequency",
                "Ex-Date", "Expected Div Per Year", "Expected Div Per Month", "Beta",
                "Div Return Contribution %",
                "Nearest Resistance (R %)", "Nearest Support (S %)", "Passed R/S", "Action"],
    "Position": ["Symbol", "History90D", "Quantity", "Avg Cost", "Latest Price", "Cost Basis", "Position Value"],
    "Performance": ["Symbol", "History90D", "Unrealized", "Unrealized %", "Dividends Received", "Total P/L",
                     "Total P/L %", "Holding Period (Years)", "Total P/L %/yr"],
    "Dividends": ["Symbol", "History90D", "Dividend Yield %", "Dividend Frequency", "Ex-Date",
                  "Expected Div Per Year", "Expected Div Per Month", "Div Return Contribution %"],
    "Classification": ["Symbol", "History90D", "Asset Class", "Portfolio Group", "Beta"],
    "Trendline": ["Symbol", "History90D", "Latest Price", "S3", "S2", "S1", "Pivot", "R1", "R2", "R3", "Action"],
    "Reference Lines": ["Symbol", "History90D", "Latest Price", "Nearest Resistance (R %)",
                         "Nearest Support (S %)", "Passed R/S", "Action"],
}
column_config = {
    "Description": st.column_config.TextColumn("Desc.", help="Description"),
    "Category": st.column_config.TextColumn("Cat.", help="Category"),
    "History90D": st.column_config.LineChartColumn("90D Trend", width=200, help="90 Day Trend"),  # 50% of "large" (400px)
    "Portfolio Group": st.column_config.TextColumn("Port. Group", help="Portfolio Group"),
    "Category Weight %": st.column_config.NumberColumn("Cat. Weight %", format="%.1f%%", help="Category Weight %"),
    "Quantity": st.column_config.NumberColumn("Shares", format="%.4f", help="Outstanding Shares"),
    "Avg Cost": st.column_config.NumberColumn("Cost/Sh", format="$%.4f", help="Cost per Share"),
    "Latest Price": st.column_config.NumberColumn("Mkt. Price", format="$%.2f", help="Market Price"),
    "Cost Basis": st.column_config.NumberColumn("Tot. Cost", format="$%.2f", help="Total Cost"),
    "Position Value": st.column_config.NumberColumn("Tot. Mkt.", format="$%.2f", help="Total Market Value"),
    "Unrealized": st.column_config.NumberColumn("Unreal.", format="$%.2f", help="Unrealized"),
    "Unrealized %": st.column_config.NumberColumn("Unreal. %", format="%.1f%%", help="Unrealized %"),
    "Dividends Received": st.column_config.NumberColumn(
        "Div Recv.", format="$%.2f",
        help=f"Actual dividends received to date (all-time), not a projection. {DIVIDEND_TAX_HELP}",
    ),
    "Total P/L": st.column_config.NumberColumn(
        format="$%.2f",
        help="Unrealized + Dividends Received (actual, all-time). Doesn't include Realized P/L from "
             "any past partial sells -- unlike Dashboard's own Total P/L column, this page only "
             "tracks currently-held positions.",
    ),
    "Total P/L %": st.column_config.NumberColumn(format="%.1f%%", help="Total P/L as a % of Total Cost."),
    "Holding Period (Years)": st.column_config.NumberColumn(
        "Held (Yrs)", format="%.1f",
        help="Years since your current position was last built from zero. Resets if you fully "
             "sold and later rebought -- this is how long you've held what you hold today, not "
             "how long ago you first ever bought this symbol.",
    ),
    "Total P/L %/yr": st.column_config.NumberColumn(
        format="%.1f%%",
        help="Total P/L % annualized by Holding Period (Years) -- lets you compare symbols held "
             "for very different lengths of time on the same yearly-rate basis.",
    ),
    "Dividend Yield %": st.column_config.NumberColumn("Div Yield %", format="%.2f%%", help="% Div per Year"),
    "Dividend Frequency": st.column_config.TextColumn("Freq.", help="Dividend Frequency"),
    "Ex-Date": st.column_config.DateColumn(
        format="DD/MM/YYYY",
        help="Most recent ex-dividend date on record. Populated for every dividend payer, "
             "including weekly/monthly funds. Highlighted when it falls in the current month "
             "-- this cycle's ex-date has already passed, so it's too late to buy in time for it.",
    ),
    "Expected Div Per Year": st.column_config.NumberColumn(
        "Expt. Div/Yr", format="$%.2f", help=f"Expected Div per Year. {DIVIDEND_TAX_HELP}",
    ),
    "Expected Div Per Month": st.column_config.NumberColumn(
        "Expt. Div/Mth", format="$%.2f", help=f"Expected Div per Month. {DIVIDEND_TAX_HELP}",
    ),
    "Beta": st.column_config.NumberColumn(format="%.2f"),
    "Weight %": st.column_config.NumberColumn(format="%.1f%%"),
    "Div Return Contribution %": st.column_config.NumberColumn(
        "Div Contrib %", format="%.2f%%", help=f"Div Return Contribution %. {DIVIDEND_TAX_HELP}",
    ),
    "Pivot": st.column_config.NumberColumn(
        "Cost/Sh", format="$%.4f",
        help="Your average cost per share -- the basis every R/S level here is built outward from, "
             "so these levels show where price sits relative to what you actually paid.",
    ),
    "R1": st.column_config.NumberColumn(format="$%.2f", help="Resistance 1, above your cost basis. Highlighted when Latest Price has reached or crossed this level."),
    "R2": st.column_config.NumberColumn(format="$%.2f", help="Resistance 2, above your cost basis. Highlighted when Latest Price has reached or crossed this level."),
    "R3": st.column_config.NumberColumn(format="$%.2f", help="Resistance 3, above your cost basis. Highlighted when Latest Price has reached or crossed this level."),
    "S1": st.column_config.NumberColumn(format="$%.2f", help="Support 1, below your cost basis. Highlighted when Latest Price has reached or crossed this level."),
    "S2": st.column_config.NumberColumn(format="$%.2f", help="Support 2, below your cost basis. Highlighted when Latest Price has reached or crossed this level."),
    "S3": st.column_config.NumberColumn(
        format="$%.2f",
        help="Support 3, below your cost basis. Highlighted when Latest Price has reached or crossed this "
             "level. Can go negative for a volatile symbol with a wide 90-day range -- a real output of "
             "the formula, not an error.",
    ),
    "Action": st.column_config.TextColumn(
        "Action", help="Click this cell to open a price chart with these levels drawn, and adjust them manually if you want.",
    ),
    "Nearest Resistance (R %)": st.column_config.TextColumn(
        help="The captured swing high nearest to current price, above it, with its live % distance. "
             "Auto-captured on first load if you haven't visited this symbol's own Auto Trendline "
             "page yet. Highlighted once current price has reached it -- see \"Passed R/S\" for "
             "the exact date.",
    ),
    "Nearest Support (S %)": st.column_config.TextColumn(
        help="The captured swing low nearest to current price, below it, with its live % distance. "
             "Auto-captured on first load if you haven't visited this symbol's own Auto Trendline "
             "page yet. Highlighted once current price has reached it -- see \"Passed R/S\" for "
             "the exact date.",
    ),
    "Passed R/S": st.column_config.DateColumn(
        format="DD/MM/YYYY",
        help="The date current price first reached either Nearest Resistance or Nearest Support -- "
             "blank if neither has been reached. If both have, shows the more recent of the two. "
             "The highlight itself lives on the Nearest Resistance/Support cell, not here -- this "
             "stays a plain, sortable date. Stays set until you Regenerate, drag, delete, or add a "
             "line on that symbol's own Auto Trendline page.",
    ),
}

def _highlight_ex_date_this_month(col: pd.Series) -> list[str]:
    """Ex-Date is always a past date (see compute above), so "this calendar
    month" alone means "already happened" -- no separate >= today check
    needed. Flags it so a monthly/weekly payer's already-passed cycle is
    visually distinct from an older month's leftover Ex-Date."""
    today = pd.Timestamp.today().normalize()
    return [
        "background-color: rgba(255, 193, 7, 0.28)" if pd.notna(v) and (v.year, v.month) == (today.year, today.month)
        else ""
        for v in col
    ]


def _highlight_pivot_crosses(row: pd.Series) -> list[str]:
    """Row-aware (axis=1), unlike _highlight_ex_date_this_month's column-aware
    (subset=[...]) shape above -- a cross depends on comparing each level to
    THAT row's own Latest Price, not a fixed reference like "today". No
    subset is passed to .style.apply() below, so this receives (and must
    return styles for) every column in the tab, not just S1..R3 -- everything
    outside S1/S2/S3/R1/R2/R3 gets "" (no style). Pivot itself is never
    highlighted -- a plain reference value, not a support/resistance level."""
    amber = "background-color: rgba(255, 193, 7, 0.28)"
    price = row.get("Latest Price")
    styles = []
    for col, val in row.items():
        # Only S1/S2/S3/R1/R2/R3 are ever evaluated -- other columns in this row can hold
        # non-scalar values (History90D is a Python list for the sparkline), and pd.isna()
        # on a list returns an array, not a bool, which breaks a plain `if` outright.
        if col not in ("R1", "R2", "R3", "S1", "S2", "S3"):
            styles.append("")
        elif pd.isna(price) or pd.isna(val):
            styles.append("")
        elif col in ("R1", "R2", "R3") and price >= val:
            styles.append(amber)
        elif col in ("S1", "S2", "S3") and price <= val:
            styles.append(amber)
        else:
            styles.append("")
    return styles


def _highlight_passed_nearest_reference(row: pd.Series) -> list[str]:
    """Row-aware (axis=1), same shape as _highlight_pivot_crosses above -- highlights
    "Nearest Resistance (R %)"/"Nearest Support (S %)" directly once THAT side's own line
    has been passed, using the hidden "_R Passed At"/"_S Passed At" helper columns (added
    to `table` alongside the visible columns below, then excluded from display via
    column_order=cols) rather than the shared "Passed R/S" date column -- that column
    alone can't tell which side passed, only the more recent date of whichever side(s)
    did, so it can't drive a per-cell highlight on its own. "Passed R/S" itself is
    intentionally left unstyled now -- still shown as a real, sortable date, just no
    longer carrying its own highlight now that the cells it summarizes carry it directly."""
    amber = "background-color: rgba(255, 193, 7, 0.28)"
    styles = []
    for col in row.index:
        if col == "Nearest Resistance (R %)" and pd.notna(row.get("_R Passed At")):
            styles.append(amber)
        elif col == "Nearest Support (S %)" and pd.notna(row.get("_S Passed At")):
            styles.append(amber)
        else:
            styles.append("")
    return styles


for tab_name, tab, cols in zip(TAB_COLUMNS.keys(), st.tabs(list(TAB_COLUMNS.keys())), TAB_COLUMNS.values()):
    with tab:
        # "_R Passed At"/"_S Passed At" ride along in `table` (for the highlighter below
        # to read) whenever either Nearest column is on this tab, but are never added to
        # `cols` -- column_order=cols on st.dataframe() further down keeps them off-screen.
        needs_passed_highlight = "Nearest Resistance (R %)" in cols or "Nearest Support (S %)" in cols
        hidden_cols = ["_R Passed At", "_S Passed At"] if needs_passed_highlight else []
        table = view[cols + hidden_cols]
        styler = None
        if "Ex-Date" in cols:
            styler = table.style.apply(_highlight_ex_date_this_month, subset=["Ex-Date"])
        if "S1" in cols:
            styler = (styler if styler is not None else table.style).apply(_highlight_pivot_crosses, axis=1)
        if needs_passed_highlight:
            styler = (styler if styler is not None else table.style).apply(
                _highlight_passed_nearest_reference, axis=1,
            )

        # "Action" tabs (Trendline, Overall, Reference Lines, Highlight) get the "Action" cell specifically wired to
        # Symbol Analysis -- see the comment above holdings["Action"]'s assignment for why
        # this is st.switch_page() driven, not a LinkColumn. selection_mode="single-cell"
        # (not "single-row") so clicking any OTHER cell in the row (Symbol, S3, etc.) just
        # focuses/highlights it like a normal read-only grid, matching the original ask --
        # only the "Action" cell itself triggers navigation. Row selection was tried first;
        # it required clicking a checkbox in a new leftmost column, not the "view" cell
        # itself, which didn't match "click view" at all.
        if "Action" in cols:
            event = st.dataframe(
                styler if styler is not None else table,
                use_container_width=True,
                hide_index=True,
                column_config=column_config,
                column_order=cols,
                on_select="rerun",
                selection_mode="single-cell",
                key=f"monitor_stocks_{tab_name}_table",
            )
            selected_cells = event.selection.cells
            if selected_cells:
                row_idx, col_name = selected_cells[0]
                if col_name == "Action":
                    selected_symbol = table.iloc[row_idx]["Symbol"]
                    st.switch_page("app_pages/symbol_analysis.py", query_params={"symbol": selected_symbol})
        else:
            st.dataframe(
                styler if styler is not None else table,
                use_container_width=True,
                hide_index=True,
                column_config=column_config,
                column_order=cols,
            )
