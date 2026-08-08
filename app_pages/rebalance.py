import os
from datetime import datetime

import pandas as pd
import plotly.express as px
import streamlit as st

from core import calculations, db, rebalance
from core.market_data import fetch_usd_thb_rate

DEFAULT_THB_RATE = 33.0

# Same xlsx + blending Monitor Stocks' own Total P/L uses (calculations.blended_dividends)
# -- actual dividends received, not the Current/New Expected Div/Yr projection already on
# this page (a different, forward-looking figure). DATA_FILE/DIVIDEND_ENTRY_TYPES are
# duplicated from app_pages/monitor_stocks.py/dashboard.py rather than centralized in
# core/ -- matches this repo's existing per-page pattern for this same xlsx load.
DATA_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "Offshore_Statements_2023-01_to_2026-06.xlsx"
)
DIVIDEND_ENTRY_TYPES = ["Dividends", "Div. Adj(NRA Withheld)", "Dividend", "Capital Distribution"]

st.title("Rebalance & Reallocate")
with st.expander("What does this page do?"):
    # $ is escaped (\$) -- Streamlit's markdown renderer otherwise treats a bare $...$
    # pair as inline LaTeX math, which mangled "$ amount"/"that $ amount" into broken
    # monospace math-mode text instead of a literal dollar sign.
    st.caption(
        "Decide how to split new money across your Dividend-classified holdings. Set a \\$ "
        "amount below, then edit each stock's % of that amount in the table and click Save "
        "changes when you're happy with it -- % allocated tracks against that \\$ amount, "
        "not your whole portfolio. Nothing is bought automatically here -- use Record Trade "
        "for the actual purchase, then come back and tick Bought. Your saved plan stays here "
        "until every row is ticked Bought, at which point it clears and a fresh one starts."
    )


@st.cache_data(ttl=300)
def _cached_dividend_holdings():
    return rebalance.get_dividend_holdings(), datetime.now()


@st.cache_data
def _dividends_received_by_symbol() -> pd.Series:
    """Actual (not projected) dividends received per symbol, all-time -- identical
    logic/source to app_pages/monitor_stocks.py's own helper of the same name (blended
    xlsx history <=cutoff + live db >cutoff). Duplicated rather than shared since each
    page's version is a small, independently cached Streamlit function tied to its own
    page-load lifecycle -- same duplication pattern DATA_FILE above already follows."""
    xls = pd.ExcelFile(DATA_FILE)
    summary = pd.read_excel(xls, "Summary")
    income = pd.read_excel(xls, "Income")
    summary["Month"] = pd.to_datetime(summary["Month"], format="%Y-%m")
    income["Month"] = pd.to_datetime(income["Month"], format="%Y-%m")
    income["Trade Date"] = pd.to_datetime(income["Trade Date"], format="%m/%d/%Y", errors="coerce")
    cutoff = summary["Month"].max() + pd.offsets.MonthEnd(0)

    blended_income = calculations.blended_dividends(income, db.fetch_dividends(), cutoff)
    div_rows = blended_income[blended_income["Entry Type"].isin(DIVIDEND_ENTRY_TYPES) & blended_income["Symbol"].notna()]
    return div_rows.groupby("Symbol")["Net Amt"].sum()


# Same 1-hour TTL/rough-reference rationale as Dashboard's own USD -> THB rate --
# this is a fully independent copy of that widget, not wired to Dashboard's value
# (which has no explicit session-state key to share safely across pages anyway).
@st.cache_data(ttl=3600)
def _cached_usd_thb_rate():
    return fetch_usd_thb_rate()


if st.button("Refresh now", help="Bypass the 5-minute cache and re-fetch live prices."):
    _cached_dividend_holdings.clear()

holdings, last_refreshed = _cached_dividend_holdings()
st.caption(f"Last refreshed: {last_refreshed.strftime('%d/%m/%Y %H:%M')}")

if holdings.empty:
    st.info("No Dividend-classified symbols currently held -- classify some in Allocation Type first.")
    st.stop()

# Total P/L = Unrealized $ + actual Dividends Received (all-time) -- same formula and
# column names as Monitor Stocks' own Total P/L, added to apply_allocation()'s "New"
# columns too (core/rebalance.py) since Dividends Received flows through unchanged.
holdings["Dividends Received"] = holdings["Symbol"].map(_dividends_received_by_symbol()).fillna(0.0)
holdings["Current Total P/L"] = holdings["Current Unrealized $"] + holdings["Dividends Received"]
holdings["Current Total P/L %"] = (
    holdings["Current Total P/L"] / holdings["Cost Basis"] * 100
).where(holdings["Cost Basis"] > 0, float("nan"))


def _pie(source_df, names_col, values_col, title):
    fig = px.pie(source_df, names=names_col, values=values_col, title=title, hole=0.4)
    fig.update_traces(textposition="inside", textinfo="percent+label")
    st.plotly_chart(fig, use_container_width=True)


DISPLAY_COLS = [
    "Symbol", "History90D", "% Reinvest", "Invest $", "Beta", "Ex-Date",
    "Current Cat Weight %", "New Cat Weight %",
    "Current Div Contrib %", "New Div Contrib %",
    "Current Expected Div/Yr", "New Expected Div/Yr",
    "Current Expected Div/Mo", "New Expected Div/Mo",
    "Current Unrealized $", "New Unrealized $",
    "Current Unrealized %", "New Unrealized %",
    "Dividends Received",
    "Current Total P/L", "New Total P/L",
    "Current Total P/L %", "New Total P/L %",
    "Bought?",
]
SNAPSHOT_KEY = "rebalance_snapshot"

# Focused slices of DISPLAY_COLS. Weight/Dividend Impact/Performance are read-only, for
# scanning. Analyze is the sole editable tab (% Reinvest/Bought?) -- see Section 3 below.
# "Performance" (renamed from "Unrealized P/L") groups Unrealized with Total P/L, matching
# Monitor Stocks' own tab shape.
TAB_COLUMNS = {
    "Weight": ["Symbol", "History90D", "Current Cat Weight %", "New Cat Weight %"],
    "Dividend Impact": ["Symbol", "History90D", "Current Div Contrib %", "New Div Contrib %",
                         "Current Expected Div/Yr", "New Expected Div/Yr",
                         "Current Expected Div/Mo", "New Expected Div/Mo"],
    "Performance": ["Symbol", "History90D", "Current Unrealized $", "New Unrealized $",
                     "Current Unrealized %", "New Unrealized %", "Dividends Received",
                     "Current Total P/L", "New Total P/L",
                     "Current Total P/L %", "New Total P/L %"],
    "Analyze": ["Symbol", "History90D", "% Reinvest", "Invest $", "Beta", "Ex-Date",
                "Current Cat Weight %", "New Cat Weight %",
                "Current Div Contrib %", "New Div Contrib %",
                "Current Expected Div/Mo", "New Expected Div/Mo",
                "Bought?"],
}


def _refresh_snapshot(holdings, plan, refreshed_at):
    # Builds the frozen state Section 1 and the table below both render from. Deliberately
    # NOT rebuilt on every script rerun (unlike Step 3/4's original version) -- only here,
    # on demand, right before a rerun that's already redrawing the table for a real reason
    # (Amount changed, Save allocation clicked, holdings refreshed). Editing a % cell alone
    # no longer calls this, which is the actual fix: hand data_editor a freshly-identical
    # DataFrame object every single keystroke, and this Streamlit version doesn't reliably
    # layer your in-flight edit on top of it -- it was found to both revert the typed value
    # and reset the grid's own scroll/selected-row position.
    pct_by_symbol = {s: plan["items"].get(s, {}).get("pct", 0.0) for s in holdings["Symbol"]}
    bought_by_symbol = {s: plan["items"].get(s, {}).get("bought", False) for s in holdings["Symbol"]}
    allocated = rebalance.apply_allocation(holdings, plan["amount"], pct_by_symbol)
    grid_source = allocated.copy()
    grid_source["% Reinvest"] = grid_source["Symbol"].map(pct_by_symbol)
    grid_source["Bought?"] = grid_source["Symbol"].map(bought_by_symbol)
    st.session_state[SNAPSHOT_KEY] = {
        "plan_id": plan["id"],
        "refreshed_at": refreshed_at,
        "allocated": allocated,
        "grid_source": grid_source[DISPLAY_COLS],
    }


@st.fragment
def _rebalance_body(holdings: pd.DataFrame, refreshed_at: datetime):
    plan = db.get_active_rebalance_plan()
    if plan is None:
        db.start_rebalance_plan(holdings["Symbol"].tolist())
        plan = db.get_active_rebalance_plan()

    def _pct(symbol):
        # A dividend symbol newly held (or newly classified Dividend) after this plan
        # started won't be in plan["items"] yet -- defaults to 0% rather than crashing;
        # it gets its own persisted row the first time it's actually edited.
        return plan["items"].get(symbol, {}).get("pct", 0.0)

    def _bought(symbol):
        return plan["items"].get(symbol, {}).get("bought", False)

    snapshot = st.session_state.get(SNAPSHOT_KEY)
    if (
        snapshot is None
        or snapshot["plan_id"] != plan["id"]
        or snapshot["refreshed_at"] != refreshed_at
    ):
        _refresh_snapshot(holdings, plan, refreshed_at)
        snapshot = st.session_state[SNAPSHOT_KEY]
    allocated = snapshot["allocated"]

    # ---------- Section 1: Summary ----------
    # Reflects the same frozen snapshot as the table below -- both update together, only
    # on Amount change / Save allocation / a live-price refresh, not on every % keystroke.
    # KPI numbers moved below the table per user request -- only the pies stay up here.
    st.subheader("Summary")
    st.caption(
        "Reflects your last-saved amount and allocation, not what you're mid-typing below "
        "-- click Save changes in the table to update these."
    )
    pie_col1, pie_col2 = st.columns(2)
    with pie_col1:
        _pie(holdings, "Symbol", "Current Cat Weight %", "Existing weight by symbol")
    with pie_col2:
        _pie(allocated, "Symbol", "New Cat Weight %", "New weight by symbol")

    existing_sector = rebalance.sector_breakdown(holdings, "Current Value").rename("Weight %").reset_index()
    new_sector = rebalance.sector_breakdown(allocated, "New Value").rename("Weight %").reset_index()
    sector_col1, sector_col2 = st.columns(2)
    with sector_col1:
        _pie(existing_sector, "Classification", "Weight %", "Existing weight by sector/asset class")
    with sector_col2:
        _pie(new_sector, "Classification", "Weight %", "New weight by sector/asset class")

    st.divider()

    # ---------- Section 2: Amount ----------
    st.subheader("Amount to invest")

    # Purely a reference calculator -- not wired to `amount` below in any way. Type a THB
    # figure, read the USD equivalent, then type that into "$ amount to invest" yourself.
    # No auto-fill, no saved state, nothing sent to the database.
    with st.expander("Quick THB → USD calculator"):
        live_thb_rate = _cached_usd_thb_rate()
        calc_col1, calc_col2 = st.columns(2)
        thb_amount = calc_col1.number_input("THB amount", min_value=0.0, step=1000.0, key="rebalance_calc_thb")
        calc_rate = calc_col2.number_input(
            "Rate (USD → THB)", min_value=0.0, value=live_thb_rate or DEFAULT_THB_RATE, step=0.1,
            key="rebalance_calc_rate", help="Pre-filled from a live quote (yfinance); edit freely if you want a different rate.",
        )
        if calc_rate > 0:
            st.caption(f"≈ **${thb_amount / calc_rate:,.2f} USD**")
        else:
            st.caption("Enter a rate above 0 to convert.")

    amount = st.number_input(
        "$ amount to invest", min_value=0.0, value=float(plan["amount"]), step=100.0,
        key="rebalance_amount",
        help="Saves immediately when changed -- unlike the table below, no Save button needed here.",
    )
    if amount != plan["amount"]:
        db.update_rebalance_plan_amount(plan["id"], amount)
        plan["amount"] = amount
        _refresh_snapshot(holdings, plan, refreshed_at)
        st.rerun(scope="fragment")

    # % allocated/remaining, from the same frozen snapshot as everything else below --
    # reflects your last Save, not what's mid-typing in the form further down (forms
    # don't rerun on every keystroke, so there's no "live" value to read here anyway).
    allocated_pct = snapshot["grid_source"]["% Reinvest"].sum()
    remaining_pct = 100.0 - allocated_pct
    mcol1, mcol2 = st.columns(2)
    mcol1.metric("% allocated", f"{allocated_pct:.1f}%")
    mcol2.metric("% remaining", f"{remaining_pct:.1f}%", help="Can go negative if you've allocated over 100%.")

    # Existing vs new KPI pairs -- driven by the frozen snapshot (same as the table's
    # New-* columns), so these update on Save allocation, not per keystroke. Explicit
    # Current/New side-by-side pairs (same visual pattern as % allocated/% remaining
    # above), not a single value + delta badge -- easier to read both numbers at once.
    current_div_mo = holdings["Current Expected Div/Mo"].sum()
    new_div_mo = allocated["New Expected Div/Mo"].sum()
    dmcol1, dmcol2 = st.columns(2)
    dmcol1.metric(
        "Expected Div/Mo", f"${current_div_mo:,.2f}",
        help="Net of 15% Thai (NRA) withholding tax, matching every other page's convention.",
    )
    dmcol2.metric(
        "New Expected Div/Mo", f"${new_div_mo:,.2f}",
        help="Expected Div/Mo after this allocation, net of 15% withholding tax.",
    )

    current_cost = holdings["Cost Basis"].sum()
    current_unrealized_pct = (holdings["Current Unrealized $"].sum() / current_cost * 100) if current_cost else float("nan")
    new_cost = allocated["New Cost Basis"].sum()
    new_unrealized_pct = (allocated["New Unrealized $"].sum() / new_cost * 100) if new_cost else float("nan")
    ucol1, ucol2 = st.columns(2)
    ucol1.metric(
        "Unrealized %", f"{current_unrealized_pct:.2f}%" if pd.notna(current_unrealized_pct) else "N/A",
        help="Across your whole dividend basket.",
    )
    ucol2.metric(
        "New Unrealized %", f"{new_unrealized_pct:.2f}%" if pd.notna(new_unrealized_pct) else "N/A",
        help="Buying more at market price dilutes this toward 0 even though your dollar "
             "unrealized gain/loss doesn't change.",
    )

    # Sum of Div Contrib % across every row -- reproduces the whole basket's blended
    # yield (see core/rebalance.py's docstring for the algebraic property). Same
    # snapshot timing as the KPI pairs above.
    current_blended_yield = holdings["Current Div Contrib %"].sum()
    new_blended_yield = allocated["New Div Contrib %"].sum()
    bcol1, bcol2 = st.columns(2)
    bcol1.metric(
        "Div Contrib %", f"{current_blended_yield:.2f}%",
        help="Net of 15% Thai (NRA) withholding tax. Sum of Div Contrib % across every "
             "row -- your whole basket's blended dividend yield today.",
    )
    bcol2.metric(
        "New Contrib %", f"{new_blended_yield:.2f}%",
        help="Blended yield after this allocation -- shows whether it raises or lowers "
             "your basket's overall yield, not just where the money is going.",
    )

    st.divider()

    # ---------- Section 3: per-stock table ----------
    st.subheader("Allocate across your dividend stocks")
    st.caption(
        "Edit % Reinvest and tick Bought? for any rows you like, then click Save changes "
        "to apply them. Bought? is just a reminder -- it doesn't insert a trade; record "
        "the real purchase yourself via Record Trade. Analyze is the only tab you can "
        "edit -- Overview/Weight/Dividend Impact/Performance are read-only views of the "
        "same data."
    )

    column_config = {
        "History90D": st.column_config.LineChartColumn("90D Trend", width=200, help="90 Day Trend"),
        "% Reinvest": st.column_config.NumberColumn(
            "% Reinvest", format="%.1f%%", min_value=0.0, max_value=100.0, step=1.0,
            help="% of the $ amount above to put into this stock. Click Save changes below to apply.",
        ),
        "Invest $": st.column_config.NumberColumn("Invest $", format="$%.2f", help="$ amount above x this row's % Reinvest."),
        "Beta": st.column_config.NumberColumn(format="%.2f"),
        "Ex-Date": st.column_config.DateColumn(
            format="DD/MM/YYYY",
            help="Most recent ex-dividend date on record, same source as Monitor Stocks. "
                 "Highlighted when it falls in the current month -- this cycle's ex-date "
                 "has already passed, so it's too late to buy in time for it.",
        ),
        "Current Cat Weight %": st.column_config.NumberColumn("Cat Wt %", format="%.1f%%", help="Current Category Weight %"),
        "New Cat Weight %": st.column_config.NumberColumn("New Cat Wt %", format="%.1f%%", help="Category Weight % after this allocation"),
        "Current Div Contrib %": st.column_config.NumberColumn(
            "Div Contrib %", format="%.2f%%",
            help="This stock's contribution to your whole dividend basket's blended yield.",
        ),
        "New Div Contrib %": st.column_config.NumberColumn(
            "New Contrib %", format="%.2f%%",
            help="Div Contrib % after this allocation.",
        ),
        "Current Expected Div/Yr": st.column_config.NumberColumn(
            "Expt. Div/Yr", format="$%.2f", help="Expected dividend per year, net of 15% Thai (NRA) withholding tax.",
        ),
        "New Expected Div/Yr": st.column_config.NumberColumn(
            "New Div/Yr", format="$%.2f", help="Expected dividend per year after this allocation, net of 15% withholding tax.",
        ),
        "Current Expected Div/Mo": st.column_config.NumberColumn(
            "Expt. Div/Mo", format="$%.2f", help="Expected dividend per month, net of 15% Thai (NRA) withholding tax.",
        ),
        "New Expected Div/Mo": st.column_config.NumberColumn(
            "New Div/Mo", format="$%.2f", help="Expected dividend per month after this allocation, net of 15% withholding tax.",
        ),
        "Current Unrealized $": st.column_config.NumberColumn("Unreal.", format="$%.2f", help="Market value minus cost basis."),
        "New Unrealized $": st.column_config.NumberColumn(
            "New Unreal.", format="$%.2f",
            help="Unchanged from today -- buying more at market price adds zero unrealized gain/loss at the moment of purchase.",
        ),
        "Current Unrealized %": st.column_config.NumberColumn("Unreal. %", format="%.1f%%", help="Unrealized $ as a % of cost basis."),
        "New Unrealized %": st.column_config.NumberColumn(
            "New Unreal. %", format="%.1f%%",
            help="Moves toward 0 after buying more -- same $ gain/loss, but spread over a larger cost basis.",
        ),
        "Dividends Received": st.column_config.NumberColumn(
            "Div Recv.", format="$%.2f", help="Actual dividends received to date (all-time), not a projection.",
        ),
        "Current Total P/L": st.column_config.NumberColumn(
            "Total P/L", format="$%.2f", help="Current Unrealized $ + Dividends Received (actual, all-time).",
        ),
        "New Total P/L": st.column_config.NumberColumn(
            "New Total P/L", format="$%.2f",
            help="Unchanged from Current Total P/L -- buying more at market price adds zero unrealized "
                 "gain/loss, and dividends already received don't change either.",
        ),
        "Current Total P/L %": st.column_config.NumberColumn(
            "Total P/L %", format="%.1f%%", help="Current Total P/L as a % of Cost Basis.",
        ),
        "New Total P/L %": st.column_config.NumberColumn(
            "New Total P/L %", format="%.1f%%",
            help="Moves toward 0 after buying more -- same $ gain, but spread over a larger cost basis.",
        ),
        "Bought?": st.column_config.CheckboxColumn(
            "Bought?",
            help="Mark once you've actually bought this -- a reminder only, doesn't "
                 "insert a trade. Doesn't lock the row; % Reinvest stays editable "
                 "either way. Once every row is ticked, this plan clears and a fresh "
                 "one starts automatically.",
        ),
    }

    tab_analyze, tab_overview, tab_weight, tab_dividend, tab_performance = st.tabs(
        ["Analyze", "Overview", "Weight", "Dividend Impact", "Performance"]
    )

    def _styled(df):
        # Same highlight as Monitor Stocks: Ex-Date is always a past date, so "this
        # calendar month" alone means "already happened" -- flags a monthly/weekly
        # payer's already-passed cycle. Works on st.data_editor too (Analyze below),
        # since Streamlit only applies Styler backgrounds to non-editable columns, and
        # Ex-Date is never one of Analyze's editable ones.
        if "Ex-Date" not in df.columns:
            return df
        today = pd.Timestamp.today().normalize()
        return df.style.apply(
            lambda col: [
                "background-color: rgba(255, 193, 7, 0.28)" if pd.notna(v) and (v.year, v.month) == (today.year, today.month)
                else "" for v in col
            ],
            subset=["Ex-Date"],
        )

    # Overview/Weight/Dividend Impact/Performance are read-only slices of the same
    # snapshot -- editing (% Reinvest, Bought?) only ever happens on Analyze below, since
    # Streamlit can't cleanly reconcile the same editable cell split across two
    # data_editor widgets shown at once. Overview shows the full DISPLAY_COLS (every
    # column that appears anywhere else on this page); the other three are focused slices.
    with tab_overview:
        st.dataframe(
            _styled(snapshot["grid_source"][DISPLAY_COLS]),
            use_container_width=True,
            hide_index=True,
            column_config=column_config,
        )
    # Explicit tab/name pairs (not TAB_COLUMNS.values() directly) -- Analyze is also a
    # key in TAB_COLUMNS but is rendered separately below as the editable tab, not here.
    for tab, name in zip((tab_weight, tab_dividend, tab_performance), ("Weight", "Dividend Impact", "Performance")):
        with tab:
            st.dataframe(
                snapshot["grid_source"][TAB_COLUMNS[name]],
                use_container_width=True,
                hide_index=True,
                column_config=column_config,
            )

    # st.form batches everything inside it (including the data_editor's in-progress edits)
    # and only releases current values to the script when the submit button is clicked --
    # this is what actually fixes the bug where typing a % and clicking a button OUTSIDE
    # the grid directly (without first pressing Tab/Enter or clicking another grid cell)
    # didn't reliably commit that edit. Trade-off: no more "live while typing" %
    # allocated/remaining -- forms don't rerun on keystrokes, so that's no longer possible;
    # everything now updates together on Save, which is also simpler to reason about.
    with tab_analyze:
        with st.form("rebalance_form"):
            edited = st.data_editor(
                _styled(snapshot["grid_source"][TAB_COLUMNS["Analyze"]]),
                use_container_width=True,
                hide_index=True,
                disabled=[c for c in TAB_COLUMNS["Analyze"] if c not in ("% Reinvest", "Bought?")],
                column_config=column_config,
                key="rebalance_editor",
            )
            submitted = st.form_submit_button(
                "Save changes", type="primary", help="Applies every % Reinvest and Bought? edit above.",
            )

    if submitted:
        changed = 0
        for _, row in edited.iterrows():
            symbol = row["Symbol"]
            new_pct = float(row["% Reinvest"]) if pd.notna(row["% Reinvest"]) else 0.0
            new_bought = bool(row["Bought?"]) if pd.notna(row["Bought?"]) else False
            if new_pct != _pct(symbol) or new_bought != _bought(symbol):
                db.update_rebalance_plan_item(plan["id"], symbol, pct=new_pct, bought=new_bought)
                changed += 1

        if not changed:
            st.caption("No changes to save.")
        else:
            plan = db.get_active_rebalance_plan()
            if plan is None:
                # Every row just got ticked Bought -- update_rebalance_plan_item() auto-
                # completed and cleared the plan. Start a fresh blank one immediately so
                # the page has something to show on the next render.
                db.start_rebalance_plan(holdings["Symbol"].tolist())
                plan = db.get_active_rebalance_plan()
                st.success("All rows bought -- plan complete! Started a fresh one.")
            else:
                st.success(f"Saved {changed} row(s).")
            _refresh_snapshot(holdings, plan, refreshed_at)
        st.rerun(scope="fragment")

    if st.button("Reset plan", help="Abandons this plan (amount, %s, and bought ticks) without requiring every row to be bought first."):
        db.reset_rebalance_plan(plan["id"])
        db.start_rebalance_plan(holdings["Symbol"].tolist())
        plan = db.get_active_rebalance_plan()
        _refresh_snapshot(holdings, plan, refreshed_at)
        st.rerun(scope="fragment")


_rebalance_body(holdings, last_refreshed)
