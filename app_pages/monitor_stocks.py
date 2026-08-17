import os

import pandas as pd
import plotly.express as px
import streamlit as st

import cached_db
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


@st.cache_data
def _cached_read_stock_profile_from_db(symbols: list[str]) -> pd.DataFrame:
    """v4.5.1 -- Monitor Stocks' normal profile-data read path: DB-first, never calls
    yfinance for a symbol that's already been captured before. Replaces v4.5's
    _cached_fetch_stock_profile, which tried a live yfinance fetch for ALL symbols on
    (roughly) every 5-minute window and only fell back to the database on failure --
    that fixed the "everything goes blank" incident (see _refresh_stock_profile_live's
    own docstring below), but still meant a real network round trip on essentially
    every normal page load. Inverted per direct user request: a plain DB read is
    already cheap and instant, so normal navigation reads market_profile_cache
    directly and only touches yfinance when "Refresh now" is explicitly clicked.

    No ttl (matches cached_db.py's own reasoning) -- the point of caching this at all
    is to skip even a redundant DB read on every rerun within a session, not to expire
    data on a timer; only .clear() (called by _refresh_stock_profile_live) or a
    changed `symbols` list busts it.

    A symbol never captured before still works out of the box, same precedent
    cached_db.reference_line_summary already established for Reference Lines: fetched
    live exactly once, right here, and saved -- simpler than the full fallback-merge
    logic below, since there's no "fall back to something" concept for a symbol with
    nothing ever cached. Either that one-time attempt succeeds (and the row appears
    here from then on) or it doesn't and the symbol is simply absent from the
    returned DataFrame -- the caller's left-merge already handles a missing row the
    same as an all-blank one, same no-regression behavior v4.5 already had."""
    cached = db.fetch_market_profile_cache()
    cached_symbols = set(cached["Symbol"]) if not cached.empty else set()
    missing = [s for s in symbols if s not in cached_symbols]
    if missing:
        live_missing = market_data.fetch_stock_profile(missing)
        successful = live_missing[live_missing["Latest Price"].notna()]
        if not successful.empty:
            db.save_market_profile_cache(successful.to_dict("records"))
        cached = db.fetch_market_profile_cache()
    return cached[cached["Symbol"].isin(symbols)]


def _refresh_stock_profile_live(symbols: list[str]) -> int:
    """v4.5.1 -- "Refresh now" button's handler: the exact live-fetch-with-fallback
    logic v4.5's _cached_fetch_stock_profile used to run on every normal load
    (prompted by a real production incident: right after v4.4.1's first deploy, Yahoo
    Finance rate-limited/blocked Streamlit Community Cloud's shared IP, and every
    yfinance-derived column went blank app-wide since there was nothing to fall back
    to yet), now explicitly opt-in instead of automatic. calculations.
    apply_market_profile_fallback() replaces any row that fails THIS live attempt with
    the last real values captured in market_profile_cache, if any. Rows that DID
    succeed live get upserted right after -- deliberately only the successful ones, so
    a still-blocked symbol never overwrites a real captured value with another blank.
    Busts _cached_read_stock_profile_from_db so the very next read reflects what was
    just fetched.

    Returns the count of symbols that failed THIS live attempt and fell back to their
    existing cached value -- the caller shows this as a warning only right after an
    actual Refresh click, not on every normal load, since a normal load never attempts
    a live fetch at all anymore and so has nothing to report failing."""
    live = market_data.fetch_stock_profile(symbols)
    cached = db.fetch_market_profile_cache()
    merged = calculations.apply_market_profile_fallback(live, cached)

    fresh_rows = merged[~merged["Stale"] & merged["Latest Price"].notna()]
    if not fresh_rows.empty:
        db.save_market_profile_cache(fresh_rows.drop(columns=["Stale", "Fetched At"]).to_dict("records"))

    _cached_read_stock_profile_from_db.clear()
    return int(merged["Stale"].sum())


def _relative_time(ts: pd.Timestamp, now: pd.Timestamp) -> str:
    """Small presentation-layer helper (not core/calculations.py -- pure string
    formatting, no business logic worth unit-testing in isolation). Mirrors the
    granularity a human actually cares about: seconds/minutes don't matter past
    "just now", hours matter within a day, days matter after that."""
    seconds = (now - ts).total_seconds()
    if seconds < 60:
        return "just now"
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    hours = int(seconds // 3600)
    if hours < 24:
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    days = int(seconds // 86400)
    return f"{days} day{'s' if days != 1 else ''} ago"


@st.cache_data
def _blended_dividend_rows() -> pd.DataFrame:
    """Actual (not projected) dividend/distribution rows, all-time, blended xlsx history
    (<=cutoff) + live db (>cutoff) -- the exact same data source and blending Dashboard's
    own per-symbol "Dividends" column already uses, just not previously loaded on this
    page. Cached like Dashboard's own load_data() -- the xlsx is static at runtime, only
    cached_db.cached_fetch_dividends() (invalidated on write) can add new rows. Row-level (not pre-summed) so both
    _dividends_received_by_symbol() and the Monthly Dividend chart below can slice it
    differently without a second xlsx load."""
    xls = pd.ExcelFile(DATA_FILE)
    summary = pd.read_excel(xls, "Summary")
    income = pd.read_excel(xls, "Income")
    summary["Month"] = pd.to_datetime(summary["Month"], format="%Y-%m")
    income["Month"] = pd.to_datetime(income["Month"], format="%Y-%m")
    income["Trade Date"] = pd.to_datetime(income["Trade Date"], format="%m/%d/%Y", errors="coerce")
    cutoff = summary["Month"].max() + pd.offsets.MonthEnd(0)

    blended_income = calculations.blended_dividends(income, cached_db.cached_fetch_dividends(), cutoff)
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
        "dividendRate). v4.5.1 -- profile data (Description/Sector/Beta/Dividends/90D "
        "history) is read straight from the database on every load, not fetched live -- "
        "click \"Refresh now\" below to pull fresh values from yfinance."
    )

db_trades = cached_db.cached_fetch_trades()
holdings = calculations.compute_current_positions(db_trades)

symbol_types = cached_db.cached_fetch_symbol_types()
holdings = holdings.merge(symbol_types, on="Symbol", how="left")
holdings["Allocation Type"] = holdings["Allocation Type"].fillna("Others")
holdings = holdings.rename(columns={"Allocation Type": "Category"})

just_refreshed_stale_count = None
if st.button("Refresh now", help="Fetch the latest live data from yfinance now (normal page loads read from the database instead)."):
    just_refreshed_stale_count = _refresh_stock_profile_live(holdings["Symbol"].tolist())
    cached_db.invalidate_reference_line_summary()

profile = _cached_read_stock_profile_from_db(holdings["Symbol"].tolist())

# v4.5.1 -- "Oldest + warning" caption, confirmed with the user via concrete chat
# mockups before building: the main line always pairs the absolute timestamp with
# relative time, since a UNIFORMLY stale batch (nobody's clicked Refresh in days, but
# every symbol shares the same old timestamp) has no single outlier to name yet still
# needs to read as clearly old. A second line names the specific outlier symbol only
# when one genuinely exists (a newly-added holding auto-captured separately from the
# rest, or one symbol's last Refresh attempt specifically failed -- see
# _refresh_stock_profile_live's Stale handling above).
freshness = calculations.describe_market_profile_freshness(profile["Fetched At"], profile["Symbol"])
if pd.isna(freshness["newest"]):
    st.caption("No profile data captured yet for any symbol -- click \"Refresh now\" above.")
else:
    now = pd.Timestamp.now()
    st.caption(
        f"Data last refreshed: {freshness['newest'].strftime('%d/%m/%Y %H:%M')} "
        f"({_relative_time(freshness['newest'], now)})"
    )
    if freshness["has_variance"]:
        st.caption(
            f"⚠ Oldest: {freshness['oldest_symbol']}, captured "
            f"{freshness['oldest'].strftime('%d/%m/%Y %H:%M')} ({_relative_time(freshness['oldest'], now)})"
        )

# v4.5.1 -- shown only right after an actual "Refresh now" click (a normal DB-first
# load never attempts a live fetch, so it has nothing to report failing). Mirrors
# v4.5's own warning, prompted by the real Yahoo Finance rate-limit incident right
# after v4.4.1 deployed, so a failed live attempt still reads as "we know, here's what
# we last captured" rather than silently presenting old data as current.
if just_refreshed_stale_count:
    st.warning(
        f"{just_refreshed_stale_count} symbol(s) couldn't be fetched live just now "
        "(likely a temporary yfinance/Yahoo issue) -- showing the last successfully "
        "captured values for those instead. Symbol/Category/Quantity/Cost columns are "
        "unaffected (they don't come from yfinance)."
    )

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

# "Action" is just a static visual label here -- the actual navigation is cell-selection
# further below (st.dataframe's on_select="rerun" + selection_mode="single-cell", checking
# whether the selected cell's column is "Action" + st.switch_page), not a LinkColumn. A
# LinkColumn's href is a plain HTML anchor rendered inside the data grid's own component
# frame, so clicking it triggers a full browser navigation rather than Streamlit's
# in-session routing -- confirmed this breaks the app's auth gate two ways: (1) a hard
# navigation spins up a brand-new, unauthenticated session, and (2) even after logging back
# in, dashboard_app.py's auth check calls st.stop() BEFORE st.navigation() ever runs, so the
# router never learns about the deep-linked URL and falls back to the default page
# (Dashboard) instead of Symbol Analysis. st.switch_page(..., query_params=...) stays
# entirely within the current session, sidestepping both problems.
holdings["Action"] = "view →"

# v4.4.1 -- Reference Lines summary (Nearest Resistance (R %)/Nearest Support (S %)):
# swing-based, nearest to current price rather than a fixed formula, and CAPTURED
# (persisted, frozen until the symbol's own Auto Trendline page saves a change) rather
# than recomputed every render.
# Auto-captures any symbol with nothing saved yet -- see cached_db.reference_line_summary's
# own docstring -- so every held symbol is watchable from this table without needing to
# visit each one's own page first.
latest_price_map = dict(zip(holdings["Symbol"], holdings["Latest Price"]))
reference_line_summary = cached_db.reference_line_summary(holdings["Symbol"].tolist(), latest_price_map)
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
    f"Showing {len(view)} of {len(holdings)} symbols. On Overall/Reference Lines/Highlight, "
    "click a row's Action cell (\"view →\") to open that symbol's price chart with its Support/"
    "Resistance levels drawn."
)

# Split from one 23-column table into focused tabs (Finviz-style column presets) --
# Symbol + History90D pinned in every tab so a row is always identifiable regardless
# of which view you're on. Each tab reuses the same underlying `view` dataframe and
# `column_config` below, just a different column subset. Overall shows nearly every
# column (the original unified table, renamed from "Overview" in v4.4.1 Improvement 3).
# The rest are focused slices of the same data.
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


def _highlight_passed_nearest_reference(row: pd.Series) -> list[str]:
    """Row-aware (axis=1) -- highlights "Nearest Resistance (R %)"/"Nearest Support (S %)"
    directly once THAT side's own line
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
        if needs_passed_highlight:
            styler = (styler if styler is not None else table.style).apply(
                _highlight_passed_nearest_reference, axis=1,
            )

        # "Action" tabs (Overall, Reference Lines, Highlight) get the "Action" cell specifically wired to
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
