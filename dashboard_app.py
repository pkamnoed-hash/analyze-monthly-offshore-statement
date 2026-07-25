import os

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from calculations import compute_realized_pl as _compute_realized_pl
from calculations import compute_roi

DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "Offshore_Statements_2023-01_to_2026-06.xlsx")

st.set_page_config(page_title="Financial Summary Dashboard", layout="wide")


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


summary, holdings, transactions, income, fees, flows = load_data(DATA_FILE)
realized_events = compute_realized_pl(transactions)

st.title("Financial Summary Dashboard")

# --- Duration filter ---
min_month = summary["Month"].min()
max_month = summary["Month"].max()

st.sidebar.header("Duration")
preset = st.sidebar.radio(
    "Quick range",
    ["All", "YTD", "Last 12M", "Last 6M", "Last 3M", "This Month", "Custom"],
    index=0,
)

if preset == "All":
    start, end = min_month, max_month
elif preset == "YTD":
    start, end = pd.Timestamp(max_month.year, 1, 1), max_month
elif preset == "Last 12M":
    start, end = max_month - pd.DateOffset(months=11), max_month
elif preset == "Last 6M":
    start, end = max_month - pd.DateOffset(months=5), max_month
elif preset == "Last 3M":
    start, end = max_month - pd.DateOffset(months=2), max_month
elif preset == "This Month":
    # "This month" means the latest month with data, not today's calendar month --
    # the two can differ (e.g. no statement imported yet for the current month).
    start, end = max_month, max_month
else:
    max_day = max_month + pd.offsets.MonthEnd(0)
    # No min_value/max_value here on purpose: Streamlit's own range validation shows a
    # blocking error and withholds the value entirely if a date outside the bound is
    # picked (e.g. via a browser autofill suggesting today's real date) -- the picker
    # then won't recover until the user manually re-enters a valid date. Clamping
    # ourselves below is more forgiving.
    date_range = st.sidebar.date_input(
        "Custom range",
        value=(min_month.date(), max_day.date()),
    )
    if isinstance(date_range, tuple) and len(date_range) == 2:
        start, end = pd.Timestamp(date_range[0]), pd.Timestamp(date_range[1])
    else:
        st.sidebar.info("Pick both a start and end date.")
        start, end = min_month, max_month

    clamped = start < min_month or end > max_month
    start = min(max(start, min_month), max_month)
    end = min(max(end, min_month), max_month)
    if clamped:
        st.sidebar.caption(
            f"Clamped to available data: {min_month.strftime('%b %Y')} – {max_month.strftime('%b %Y')}."
        )
    # Data is stored monthly, so snap the picked dates to whichever months they fall in.
    start = pd.Timestamp(start.year, start.month, 1)
    end = pd.Timestamp(end.year, end.month, 1)

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
inc = income.loc[i_mask]
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

dividend_types = ["Dividends", "Div. Adj(NRA Withheld)"]
div_in_range = inc[inc["Entry Type"].isin(dividend_types) & inc["Symbol"].notna()]
dividends_by_symbol = div_in_range.groupby("Symbol")["Net Amt"].sum()

total_realized = realized_by_symbol.sum()
total_unrealized = latest_holdings["Unrealized"].sum()
total_dividends = dividends_by_symbol.sum()
total_interest = inc[inc["Entry Type"] == "Credit/Margin Interest"]["Net Amt"].sum()
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
c1.metric("Portfolio Value", f"${ending_value:,.2f}")
c2.metric("Net Deposits (tracked)", f"${net_flows:,.2f}")
c3.metric("Investment Gain/Loss", f"${investment_gain:,.2f}",
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

c4, c5, c6, c7 = st.columns(4)
c4.metric("Dividends + Interest", f"${(total_dividends + total_interest):,.2f}")
c5.metric("Realized P/L (est.)", f"${total_realized:,.2f}")
c6.metric("Unrealized P/L", f"${total_unrealized:,.2f}")
c7.metric("Total Fees", f"${total_fees:,.2f}")

if abs(balance_based_gain - investment_gain) > 1:
    st.caption(
        f"Note: (Portfolio Value − Net Deposits) gives ${balance_based_gain:,.2f}, which differs from the "
        f"Investment Gain/Loss above by ${balance_based_gain - investment_gain:,.2f}. Most of that gap is the "
        "average-cost Realized P/L estimate above differing from the broker's own specific-lot ST/LT figures "
        "(expected — see the Realized P/L caveat); the remainder is minor rounding accumulated across months. "
        "The Investment Gain/Loss figure is the more reliable number for tracking performance."
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

st.divider()

def for_display(df):
    df = df.copy()
    if "Month" in df.columns:
        df["Month"] = df["Month"].dt.strftime("%Y-%m")
    if "Trade Date" in df.columns:
        df["Trade Date"] = df["Trade Date"].dt.strftime("%Y-%m-%d")
    return df


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

    pl_chart_df = by_symbol_view[(by_symbol_view["Total P/L"] != 0) | (by_symbol_view["Market Value"] != 0)].copy()
    pl_chart_df = pl_chart_df.sort_values("Total P/L")
    if not pl_chart_df.empty:
        fig5 = px.bar(
            pl_chart_df, x="Total P/L", y="Symbol", orientation="h",
            color=pl_chart_df["Total P/L"] >= 0,
            color_discrete_map={True: "#2ca02c", False: "#d62728"},
            title="Total P/L by Symbol (Realized + Unrealized + Dividends)",
            height=max(400, 18 * len(pl_chart_df)),
        )
        fig5.update_layout(showlegend=False, yaxis_title="", xaxis_title="USD")
        st.plotly_chart(fig5, use_container_width=True)

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
