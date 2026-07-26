import os

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

import db
from calculations import blended_dividends, blended_realized_pl
from calculations import compute_realized_pl as _compute_realized_pl
from calculations import compute_roi

DATA_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "Offshore_Statements_2023-01_to_2026-06.xlsx"
)


@st.cache_data
def load_data(path):
    xls = pd.ExcelFile(path)
    summary = pd.read_excel(xls, "Summary")
    holdings = pd.read_excel(xls, "Holdings")
    transactions = pd.read_excel(xls, "Transactions")
    income = pd.read_excel(xls, "Income")
    fees = pd.read_excel(xls, "Fees")
    flows = pd.read_excel(xls, "Deposits & Withdrawals")

    summary["Month"] = pd.to_datetime(summary["Month"], format="%Y-%m")
    holdings["Month"] = pd.to_datetime(holdings["Month"], format="%Y-%m")
    holdings["Quantity"] = pd.to_numeric(holdings["Quantity"], errors="coerce")
    for df in (transactions, income, fees, flows):
        df["Month"] = pd.to_datetime(df["Month"], format="%Y-%m")
        df["Trade Date"] = pd.to_datetime(df["Trade Date"], format="%m/%d/%Y", errors="coerce")

    return summary, holdings, transactions, income, fees, flows


@st.cache_data
def compute_realized_pl(transactions):
    return _compute_realized_pl(transactions)


def for_display(df):
    df = df.copy()
    if "Month" in df.columns:
        df["Month"] = df["Month"].dt.strftime("%Y-%m")
    if "Trade Date" in df.columns:
        df["Trade Date"] = df["Trade Date"].dt.strftime("%Y-%m-%d")
    return df


summary, holdings, transactions, income, fees, flows = load_data(DATA_FILE)

st.title("Financial Summary Dashboard")

# --- Duration filter ---
min_month = summary["Month"].min()
max_month = summary["Month"].max()
max_day = max_month + pd.offsets.MonthEnd(0)  # last actual calendar date covered by official data

# Blending cutoff: everything up to and including this date is the audited xlsx history;
# anything after it is live data entered through this app (Record Trade / Record Dividend).
# Not cached like load_data() -- db.fetch_trades()/fetch_dividends() read the live SQLite
# file directly each run, since a Record Trade/Dividend save (Steps 4-6) needs to show up
# immediately rather than behind a stale cache.
cutoff = max_day
db_trades = db.fetch_trades()
db_dividends = db.fetch_dividends()
realized_events = blended_realized_pl(compute_realized_pl(transactions), db_trades, cutoff)
blended_income = blended_dividends(income, db_dividends, cutoff)

# The Duration filter below must be able to reach live trades/dividends dated after the
# last official statement -- otherwise they're already correctly included in the blended
# frames above, but silently excluded again right here by a range that stops at max_day.
# data_end extends the filter's upper bound to cover them; max_day/max_month/cutoff stay
# as the "last official statement" boundary used for blending-source selection and for
# labeling Portfolio Value/Unrealized P/L/Holdings, which still don't extend past it.
latest_live_date = pd.concat([db_trades["Trade Date"], db_dividends["Trade Date"]]).max()
data_end = max(max_day, latest_live_date) if pd.notna(latest_live_date) else max_day
data_end_month = pd.Timestamp(data_end.year, data_end.month, 1)

st.sidebar.header("Duration")

# Months back from the latest available month, per preset. "Past Week" and "Past Month"
# both resolve to just that one month -- data is stored monthly, so there's no finer
# granularity to tell them apart at.
PRESET_MONTHS_BACK = {
    "All": None,
    "This Month": 0,
    "Past Week": 0,
    "Past Month": 0,
    "Past 3 Months": 2,
    "Past 6 Months": 5,
    "Past Year": 11,
    "Past 2 Years": 23,
}
preset = st.sidebar.selectbox("Choose a date range", list(PRESET_MONTHS_BACK.keys()), index=0)

months_back = PRESET_MONTHS_BACK[preset]
if months_back is None:
    default_start = min_month
else:
    # Anchored to the latest month with EITHER official or live data (data_end_month),
    # not today's real calendar date -- those can still differ, e.g. no statement
    # imported yet for the current month AND nothing logged live for it either.
    default_start = data_end_month - pd.DateOffset(months=months_back)

# No min_value/max_value here on purpose: Streamlit's own range validation shows a
# blocking error and withholds the value entirely if a date outside the bound is picked
# (e.g. via a browser autofill suggesting today's real date) -- the picker then won't
# recover until the user manually re-enters a valid date. Clamping ourselves below is
# more forgiving -- and load-bearing here, since a browser extension has been observed
# overwriting this field with its own guess independent of what we set as the default.
# The key is re-seeded per preset so picking a new preset refreshes the shown range,
# while manual edits to the range persist until the preset changes again.
date_range = st.sidebar.date_input(
    "Custom range",
    value=(default_start.date(), data_end.date()),
    key=f"custom_range_{preset}",
)
if isinstance(date_range, tuple) and len(date_range) == 2:
    start, end = pd.Timestamp(date_range[0]), pd.Timestamp(date_range[1])
else:
    st.sidebar.info("Pick both a start and end date.")
    start, end = min_month, data_end_month

clamped = start < min_month or end > data_end
start = min(max(start, min_month), data_end_month)
end = min(max(end, min_month), data_end)
if clamped:
    st.sidebar.caption(
        f"Clamped to available data: {min_month.strftime('%b %Y')} – {data_end_month.strftime('%b %Y')}."
    )
# Data is stored monthly, so snap the picked dates to whichever months they fall in.
start = pd.Timestamp(start.year, start.month, 1)
end = pd.Timestamp(end.year, end.month, 1)

st.sidebar.header("Display")
thb_rate = st.sidebar.number_input(
    "USD → THB rate", min_value=0.0, value=33.0, step=0.1,
    help="Adjust to today's rate. Used only for the small THB reference shown under each dollar figure "
         "below -- it's a single rough rate applied uniformly, not a historically accurate one for older months.",
)


def thb(usd):
    return f"≈ ฿{usd * thb_rate:,.0f}"


mask = (summary["Month"] >= start) & (summary["Month"] <= end)
s = summary.loc[mask].sort_values("Month")

if s.empty:
    st.warning("No data in the selected range.")
    st.stop()

h_mask = (holdings["Month"] >= start) & (holdings["Month"] <= end)
t_mask = (transactions["Month"] >= start) & (transactions["Month"] <= end)
i_mask = (income["Month"] >= start) & (income["Month"] <= end)
f_mask = (fees["Month"] >= start) & (fees["Month"] <= end)
fl_mask = (flows["Month"] >= start) & (flows["Month"] <= end)

h = holdings.loc[h_mask]
t = transactions.loc[t_mask]
inc = income.loc[i_mask]  # xlsx-only, unmodified -- feeds the historical Income & Fees tab display
fe = fees.loc[f_mask]
fl = flows.loc[fl_mask]

st.caption(f"Showing **{start.strftime('%b %Y')} – {end.strftime('%b %Y')}**")

# --- Shared per-symbol aggregates (used by KPIs, allocation chart, and By Symbol tab) ---
latest_month = h["Month"].max()
latest_holdings = h[(h["Month"] == latest_month) & (h["Symbol"] != "*Cash")].copy()
latest_holdings = latest_holdings[latest_holdings["Market Value"].notna()]
latest_holdings = latest_holdings.sort_values("Market Value", ascending=False)

end_of_period = end + pd.offsets.MonthEnd(0)
realized_in_range = realized_events[(realized_events["Trade Date"] >= start) & (realized_events["Trade Date"] <= end_of_period)]
realized_by_symbol = realized_in_range.groupby("Symbol")["Realized P/L"].sum()

# blended_income spans full history (xlsx <= cutoff, live db > cutoff) -- range-filter it the
# same way inc is filtered above, then widen the Entry Type lists to recognize both the xlsx
# vocabulary ("Dividends"/"Div. Adj(NRA Withheld)"/"Credit/Margin Interest") and the db
# vocabulary ("Dividend"/"Capital Distribution"/"Interest") for the same real-world categories.
income_in_range = blended_income[(blended_income["Trade Date"] >= start) & (blended_income["Trade Date"] <= end_of_period)]
dividend_types = ["Dividends", "Div. Adj(NRA Withheld)", "Dividend", "Capital Distribution"]
div_in_range = income_in_range[income_in_range["Entry Type"].isin(dividend_types) & income_in_range["Symbol"].notna()]
dividends_by_symbol = div_in_range.groupby("Symbol")["Net Amt"].sum()

total_realized = realized_by_symbol.sum()
total_unrealized = latest_holdings["Unrealized"].sum()
total_dividends = dividends_by_symbol.sum()
total_interest = income_in_range[income_in_range["Entry Type"].isin(["Credit/Margin Interest", "Interest"])]["Net Amt"].sum()
investment_gain = total_realized + total_unrealized + total_dividends + total_interest

# --- KPI cards ---
latest = s.iloc[-1]
# "Total Market Value ($)" already equals Ending Cash + Long (verified: holds exactly in
# every month of this statement) — it IS the portfolio value, not a component to add cash to.
ending_value = latest["Total Market Value ($)"]

# Starting value = portfolio's Total Market Value as of the month before this period began.
# "Beginning Balance ($)" is NOT this — per the statement's own Validation sheet, it's just
# the prior month's Ending Cash carried over, so using it would ignore all invested holdings.
prior_rows = summary[summary["Month"] < start].sort_values("Month")
start_value = prior_rows.iloc[-1]["Total Market Value ($)"] if not prior_rows.empty else 0.0

total_deposits = fl["Net Amt"].clip(lower=0).sum()
total_withdrawals = -fl["Net Amt"].clip(upper=0).sum()

# Regulatory fees (TAF/REG/CAT, Fees sheet) plus per-trade commissions (Transactions sheet)
# -- the latter isn't in the Fees sheet at all, so "Total Fees" without it understates true
# trading cost by ~80x over this account's history. Commissions are already folded into
# Realized P/L's cost-basis math (calculations.py), so this is a display-only figure and
# doesn't get double-subtracted anywhere else.
total_fees = (fe["Net Amt"].sum() if "Net Amt" in fe else 0) - t["Commission"].fillna(0).sum()
net_flows = total_deposits - total_withdrawals
balance_based_gain = ending_value - start_value - net_flows

# ROI over the selected duration: gain ÷ capital base (starting value + money added during
# the period). Using start_value avoids a misleading result for periods with little/no new
# deposits but substantial capital already invested (e.g. "Last 3M").
capital_base = start_value + net_flows
period_days = max((end_of_period - start).days + 1, 1)
roi_pct, annualized_roi_pct = compute_roi(investment_gain, capital_base, period_days)

c1, c2, c3, c4 = st.columns(4)
c1.metric("Portfolio Value", f"${ending_value:,.2f}", delta=thb(ending_value), delta_color="off")
c2.metric("Net Deposits (tracked)", f"${net_flows:,.2f}", delta=thb(net_flows), delta_color="off")
c3.metric("Investment Gain/Loss", f"${investment_gain:,.2f}", delta=thb(investment_gain), delta_color="off",
           help="Realized P/L + Unrealized P/L + Dividends + Interest. This is the recommended headline "
                "number — it's built from trade prices and holding values, so it isn't affected by how "
                "the statement labels cash movements.")
if roi_pct is not None:
    roi_delta = f"{annualized_roi_pct:,.2f}%/yr annualized" if annualized_roi_pct is not None else None
    c4.metric("ROI (period)", f"{roi_pct:,.2f}%", delta=roi_delta, delta_color="off",
              help="Investment Gain/Loss ÷ (Starting Value + Net Deposits) for the selected duration. "
                   "The annualized figure compounds this rate to a 1-year equivalent so periods of "
                   "different lengths (YTD, Last 3M, All, etc.) can be compared on the same basis.")
else:
    c4.metric("ROI (period)", "N/A",
              help="No capital base (starting value + net deposits) in this period to compute ROI against.")

# -- Income (recurring cash yield: dividends/interest) --
n_months = len(s)
avg_monthly_dividend = total_dividends / n_months

c5, c6, c7 = st.columns(3)
c5.metric("Dividends", f"${total_dividends:,.2f}", delta=thb(total_dividends), delta_color="off",
          help="Net of the 15% Thai (NRA) withholding tax. Only dividends -- not interest -- are "
               "attributed per-symbol in the By Symbol tab below.")
c6.metric("Avg. Monthly Dividend", f"${avg_monthly_dividend:,.2f}", delta=thb(avg_monthly_dividend), delta_color="off",
          help=f"Dividends ÷ {n_months} month{'s' if n_months != 1 else ''} in the selected period.")
c7.metric("Interest", f"${total_interest:,.2f}", delta=thb(total_interest), delta_color="off",
          help="Cash-sweep/margin interest. This account rarely earns any in real time -- most of its "
               "history shows $0 here except a one-time year-end reallocation catching up prior months.")

# -- Capital gains/losses and costs (price movements, separate from income above) --
c8, c9, c10 = st.columns(3)
c8.metric("Realized P/L (est.)", f"${total_realized:,.2f}", delta=thb(total_realized), delta_color="off")
c9.metric("Unrealized P/L", f"${total_unrealized:,.2f}", delta=thb(total_unrealized), delta_color="off")
c10.metric("Total Fees", f"${total_fees:,.2f}", delta=thb(total_fees), delta_color="off",
           help="Fees sheet (REG/TAF/CAT/ADR, etc.) + Transactions sheet's Commission column. "
                "The Fees tab below only shows the former, so summing just that table will "
                "come up short of this figure by however much was paid in trade commissions.")

if abs(balance_based_gain - investment_gain) > 1:
    st.caption(
        f"Note: (Portfolio Value − Net Deposits) gives ${balance_based_gain:,.2f}, which differs from the "
        f"Investment Gain/Loss above by ${balance_based_gain - investment_gain:,.2f}. Most of that gap is the "
        "average-cost Realized P/L estimate above differing from the broker's own specific-lot ST/LT figures "
        "(expected — see the Realized P/L caveat); the remainder is minor rounding accumulated across months. "
        "The Investment Gain/Loss figure is the more reliable number for tracking performance."
    )

# --- Since Last Statement: live trades/dividends logged through this app, not yet covered by
# an official broker PDF. Portfolio Value/Unrealized P/L above stay as of the last statement
# (see caption) -- this panel is where new activity in between statements is actually visible.
live_trades = db_trades[db_trades["Trade Date"] > cutoff].copy()
live_dividends = db_dividends[db_dividends["Trade Date"] > cutoff]

# Joined on id (compute_fifo_realized_pl threads the originating trade's id through) so each
# trade shows the Realized P/L it actually produced, not just an aggregate elsewhere on the
# page -- blank for buys (nothing realized), the FIFO-computed value for sells/corp actions.
live_realized = realized_events.dropna(subset=["id"])[["id", "Realized P/L"]]
live_trades["id"] = live_trades["id"].astype(float)
live_trades = live_trades.merge(live_realized, on="id", how="left")

with st.expander(
    f"Since Last Statement ({cutoff.strftime('%b %Y')})",
    expanded=not (live_trades.empty and live_dividends.empty),
):
    if live_trades.empty and live_dividends.empty:
        st.caption("No new activity logged since the last official statement.")
    else:
        lc1, lc2 = st.columns(2)
        with lc1:
            st.write(f"**{len(live_trades)} trade(s)** — net ${live_trades['Amount'].sum():,.2f}")
            st.dataframe(
                for_display(live_trades[["Trade Date", "Symbol", "Side", "Quantity", "Price", "Amount", "Realized P/L"]]),
                use_container_width=True, hide_index=True,
                column_config={"Realized P/L": st.column_config.NumberColumn(format="$%.2f")},
            )
            if live_trades["Realized P/L"].notna().any():
                st.caption("Realized P/L reflects FIFO matching against your oldest open lot for that symbol.")
        with lc2:
            st.write(f"**{len(live_dividends)} dividend/interest row(s)** — total ${live_dividends['Net Amt'].sum():,.2f}")
            st.dataframe(
                for_display(live_dividends[["Trade Date", "Symbol", "Entry Type", "Net Amt"]]),
                use_container_width=True, hide_index=True,
            )
    st.caption(
        "Portfolio Value, Unrealized P/L, and Holdings above stay as of the last official statement -- "
        "new positions won't be reflected there until the next one is processed."
    )

st.divider()

# --- Trends over time ---
col1, col2 = st.columns(2)

with col1:
    # "Total Market Value ($)" is already the full portfolio value (cash + long positions) —
    # plot it alongside its two components instead of adding cash to it a second time.
    s_chart = s.rename(columns={"Total Market Value ($)": "Portfolio Value"})
    fig = px.line(s_chart, x="Month", y=["Portfolio Value", "Ending Cash ($)", "Long ($)"],
                  title="Portfolio Value Over Time", markers=True)
    fig.update_layout(legend_title_text="", yaxis_title="USD")
    st.plotly_chart(fig, use_container_width=True)

with col2:
    flow_by_month = fl.groupby(fl["Month"].dt.to_period("M"))["Net Amt"].sum().reset_index()
    flow_by_month["Month"] = flow_by_month["Month"].dt.to_timestamp()
    fig2 = px.bar(flow_by_month, x="Month", y="Net Amt", title="Deposits / Withdrawals by Month")
    fig2.update_layout(yaxis_title="USD")
    st.plotly_chart(fig2, use_container_width=True)

col3, col4 = st.columns(2)

with col3:
    income_cols = [c for c in ["Dividend ($)", "Interest ($)", "Realized ST Net ($)", "Realized LT Net ($)"] if c in s.columns]
    if income_cols:
        fig3 = px.bar(s, x="Month", y=income_cols, title="Income Breakdown by Month", barmode="stack")
        fig3.update_layout(legend_title_text="", yaxis_title="USD")
        st.plotly_chart(fig3, use_container_width=True)

with col4:
    if not latest_holdings.empty:
        top_n = 10
        top = latest_holdings.head(top_n)
        rest_value = latest_holdings["Market Value"].iloc[top_n:].sum()
        if rest_value > 0:
            other_row = pd.DataFrame([{"Symbol": f"Other ({len(latest_holdings) - top_n})", "Market Value": rest_value}])
            plot_df = pd.concat([top[["Symbol", "Market Value"]], other_row], ignore_index=True)
        else:
            plot_df = top[["Symbol", "Market Value"]]
        fig4 = px.pie(plot_df, names="Symbol", values="Market Value",
                      title=f"Current Allocation ({latest_month.strftime('%b %Y')})", hole=0.4)
        fig4.update_traces(textposition="inside", textinfo="percent+label")
        st.plotly_chart(fig4, use_container_width=True)
        st.caption("See the **By Symbol** tab below for holding, P/L, and dividend detail per symbol.")

# Net of the 15% Thai withholding tax, matching the Dividends / Avg. Monthly Dividend KPIs
# above -- deliberately not the Summary sheet's "Dividend ($)" column used in the Income
# Breakdown chart, which is gross (pre-withholding) and would be inconsistent with those.
dividend_by_month = div_in_range.groupby(div_in_range["Trade Date"].dt.to_period("M"))["Net Amt"].sum().reset_index()
dividend_by_month = dividend_by_month.rename(columns={"Trade Date": "Month"})
dividend_by_month["Month"] = dividend_by_month["Month"].dt.to_timestamp()
fig_div = px.bar(dividend_by_month, x="Month", y="Net Amt", title="Monthly Dividend (net of 15% withholding tax)",
                  color_discrete_sequence=["#1f77b4"])
fig_div.add_hline(y=avg_monthly_dividend, line_dash="dot", line_color="gray",
                   annotation_text=f"Avg: ${avg_monthly_dividend:,.2f}/mo", annotation_position="top left")
fig_div.update_layout(yaxis_title="USD", xaxis_title="Month")
st.plotly_chart(fig_div, use_container_width=True)

# Average-cost estimate (calculations.py), matching the "Realized P/L (est.)" KPI -- not the
# Summary sheet's broker specific-lot ST/LT columns already shown in Income Breakdown, which
# is a different figure (see docs/METHODOLOGY.md). Realized is event-driven and can spike in
# a single month; Unrealized is a smoother month-to-month mark-to-market balance -- shown as
# grouped (not stacked) bars so one doesn't get summed into or read as part of the other.
realized_by_month = realized_in_range.groupby(realized_in_range["Trade Date"].dt.to_period("M"))["Realized P/L"].sum().reset_index()
realized_by_month = realized_by_month.rename(columns={"Trade Date": "Month"})
realized_by_month["Month"] = realized_by_month["Month"].dt.to_timestamp()
unrealized_by_month = h[h["Symbol"] != "*Cash"].groupby("Month")["Unrealized"].sum().reset_index()
unrealized_by_month = unrealized_by_month.rename(columns={"Unrealized": "Unrealized P/L"})
pl_by_month = pd.merge(realized_by_month, unrealized_by_month, on="Month", how="outer").fillna(0).sort_values("Month")

fig_pl = px.bar(pl_by_month, x="Month", y=["Realized P/L", "Unrealized P/L"],
                 title="Realized vs Unrealized P/L by Month (est.)", barmode="group")
fig_pl.update_layout(legend_title_text="", yaxis_title="USD", xaxis_title="Month")
st.plotly_chart(fig_pl, use_container_width=True)

st.divider()

# --- By-symbol detail table (reuses aggregates computed above for the KPIs) ---
symbol_universe = sorted(
    set(latest_holdings["Symbol"]) | set(realized_by_symbol.index) | set(dividends_by_symbol.index)
)

# Last known price/description per symbol, from the full (unfiltered) holdings history —
# used so exited positions show their last traded price instead of $0.
last_known = (
    holdings[holdings["Symbol"] != "*Cash"]
    .sort_values("Month")
    .groupby("Symbol", as_index=False)
    .last()[["Symbol", "Description", "Market Price", "Cost Price"]]
)

by_symbol = pd.DataFrame({"Symbol": symbol_universe})
by_symbol = by_symbol.merge(last_known, on="Symbol", how="left")
by_symbol = by_symbol.merge(
    latest_holdings[["Symbol", "Quantity", "Market Value", "Unrealized"]],
    on="Symbol", how="left",
)
by_symbol["Realized P/L"] = by_symbol["Symbol"].map(realized_by_symbol).fillna(0.0)
by_symbol["Dividends"] = by_symbol["Symbol"].map(dividends_by_symbol).fillna(0.0)
for col in ["Quantity", "Market Value", "Unrealized"]:
    by_symbol[col] = by_symbol[col].fillna(0.0)
by_symbol["Market Price"] = by_symbol["Market Price"].fillna(0.0)
by_symbol["Cost Price"] = by_symbol["Cost Price"].fillna(0.0)
by_symbol["Description"] = by_symbol["Description"].fillna("")
cost_basis = by_symbol["Market Value"] - by_symbol["Unrealized"]
by_symbol["Unrealized %"] = by_symbol["Unrealized"] / cost_basis.replace(0, float("nan")) * 100
by_symbol["Total P/L"] = by_symbol["Realized P/L"] + by_symbol["Unrealized"] + by_symbol["Dividends"]
by_symbol["Status"] = by_symbol["Quantity"].apply(lambda q: "Holding" if q > 1e-6 else "Sold")
by_symbol = by_symbol.sort_values("Market Value", ascending=False)
symbol_status = dict(zip(by_symbol["Symbol"], by_symbol["Status"]))

# --- Data tables ---
tab0, tab1, tab2, tab3, tab4 = st.tabs(["By Symbol", "Summary", "Holdings", "Transactions", "Income & Fees"])

with tab0:
    st.caption(
        f"Holding snapshot as of **{latest_month.strftime('%b %Y')}**; Realized P/L and Dividends are summed over "
        f"**{start.strftime('%b %Y')} – {end.strftime('%b %Y')}**. Realized P/L is an average-cost estimate and "
        "may differ slightly from the broker's official Realized ST/LT figures. Interest income "
        f"(${total_interest:,.2f} in this period) isn't tied to a symbol, so it's excluded from this table but "
        "is included in the Investment Gain/Loss KPI above. For **Sold** positions, Market Price and Cost Price "
        "show the last values from the month before the position was fully closed, not a live quote."
    )

    fcol1, fcol2 = st.columns([1, 2])
    with fcol1:
        symbol_search = st.text_input("Search symbol", placeholder="Search symbol or name...", label_visibility="collapsed")
    with fcol2:
        n_holding = (by_symbol["Status"] == "Holding").sum()
        n_sold = (by_symbol["Status"] == "Sold").sum()
        status_filter = st.radio(
            "Status", [f"All ({len(by_symbol)})", f"Holding ({n_holding})", f"Sold ({n_sold})"],
            horizontal=True, label_visibility="collapsed",
        )
    if status_filter.startswith("Holding"):
        by_symbol_view = by_symbol[by_symbol["Status"] == "Holding"]
    elif status_filter.startswith("Sold"):
        by_symbol_view = by_symbol[by_symbol["Status"] == "Sold"]
    else:
        by_symbol_view = by_symbol
    if symbol_search:
        by_symbol_view = by_symbol_view[
            by_symbol_view["Symbol"].str.contains(symbol_search, case=False, na=False)
            | by_symbol_view["Description"].str.contains(symbol_search, case=False, na=False)
        ]

    display_cols = ["Symbol", "Status", "Description", "Quantity", "Market Price", "Market Value", "Cost Price",
                     "Unrealized", "Unrealized %", "Realized P/L", "Dividends", "Total P/L"]
    st.dataframe(
        by_symbol_view[display_cols],
        use_container_width=True,
        hide_index=True,
        column_config={
            "Quantity": st.column_config.NumberColumn(format="%.4f"),
            "Market Price": st.column_config.NumberColumn(format="$%.2f"),
            "Market Value": st.column_config.NumberColumn(format="$%.2f"),
            "Cost Price": st.column_config.NumberColumn(format="$%.2f"),
            "Unrealized": st.column_config.NumberColumn(format="$%.2f"),
            "Unrealized %": st.column_config.NumberColumn(format="%.1f%%"),
            "Realized P/L": st.column_config.NumberColumn(format="$%.2f"),
            "Dividends": st.column_config.NumberColumn(format="$%.2f"),
            "Total P/L": st.column_config.NumberColumn(format="$%.2f"),
        },
    )

with tab1:
    st.dataframe(for_display(s), use_container_width=True)

with tab2:
    st.dataframe(for_display(h.sort_values(["Month", "Symbol"])), use_container_width=True)

with tab3:
    fc1, fc2 = st.columns([2, 1])
    with fc1:
        symbols = ["All"] + sorted(t["Symbol"].dropna().unique().tolist())
        sel_symbol = st.selectbox(
            "Filter by symbol", symbols,
            format_func=lambda sym: sym if sym == "All" else f"{sym} — {symbol_status.get(sym, 'n/a')}",
        )
    with fc2:
        sel_status = st.selectbox("Filter by status", ["All", "Holding", "Sold"])

    t_view = t if sel_symbol == "All" else t[t["Symbol"] == sel_symbol]
    if sel_status != "All":
        t_view = t_view[t_view["Symbol"].map(symbol_status) == sel_status]

    t_view = t_view.copy()
    t_view.insert(t_view.columns.get_loc("Symbol") + 1, "Status", t_view["Symbol"].map(symbol_status).fillna("n/a"))
    st.dataframe(for_display(t_view.sort_values("Trade Date")), use_container_width=True)

with tab4:
    ic1, ic2 = st.columns(2)
    with ic1:
        st.subheader("Income")
        st.dataframe(for_display(inc.sort_values("Trade Date")), use_container_width=True)
    with ic2:
        st.subheader("Fees")
        st.dataframe(for_display(fe.sort_values("Trade Date")), use_container_width=True)
