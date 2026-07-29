from datetime import datetime

import pandas as pd
import plotly.express as px
import streamlit as st

from core import db, rebalance

st.title("Rebalance & Reallocate")
st.caption(
    "Decide how to split new money across your Dividend-classified holdings. Set a $ "
    "amount below, then edit each stock's % of that amount in the table and click Save "
    "changes when you're happy with it -- % allocated tracks against that $ amount, "
    "not your whole portfolio. Nothing is bought automatically here -- use Record Trade "
    "for the actual purchase, then come back and tick Bought. Your saved plan stays here "
    "until every row is ticked Bought, at which point it clears and a fresh one starts."
)


@st.cache_data(ttl=300)
def _cached_dividend_holdings():
    return rebalance.get_dividend_holdings(), datetime.now()


if st.button("Refresh now", help="Bypass the 5-minute cache and re-fetch live prices."):
    _cached_dividend_holdings.clear()

holdings, last_refreshed = _cached_dividend_holdings()
st.caption(f"Last refreshed: {last_refreshed.strftime('%d/%m/%Y %H:%M')}")

if holdings.empty:
    st.info("No Dividend-classified symbols currently held -- classify some in Allocation Type first.")
    st.stop()


def _pie(source_df, names_col, values_col, title):
    fig = px.pie(source_df, names=names_col, values=values_col, title=title, hole=0.4)
    fig.update_traces(textposition="inside", textinfo="percent+label")
    st.plotly_chart(fig, use_container_width=True)


DISPLAY_COLS = [
    "Symbol", "History90D", "% Reinvest", "Invest $",
    "Current Cat Weight %", "New Cat Weight %",
    "Current Div Contrib %", "New Div Contrib %",
    "Current Expected Div/Yr", "New Expected Div/Yr",
    "Current Expected Div/Mo", "New Expected Div/Mo",
    "Current Unrealized $", "New Unrealized $",
    "Current Unrealized %", "New Unrealized %",
    "Bought?",
]
SNAPSHOT_KEY = "rebalance_snapshot"


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

    # Existing vs new KPI pair -- driven by the frozen snapshot (same as the table's
    # New-* columns), so these update on Save allocation, not per keystroke.
    kcol1, kcol2 = st.columns(2)
    current_div_mo = holdings["Current Expected Div/Mo"].sum()
    new_div_mo = allocated["New Expected Div/Mo"].sum()
    kcol1.metric(
        "Expected Div/Mo", f"${new_div_mo:,.2f}",
        delta=f"${new_div_mo - current_div_mo:,.2f}",
        help="Net of 15% Thai (NRA) withholding tax, matching every other page's convention.",
    )

    current_cost = holdings["Cost Basis"].sum()
    current_unrealized_pct = (holdings["Current Unrealized $"].sum() / current_cost * 100) if current_cost else float("nan")
    new_cost = allocated["New Cost Basis"].sum()
    new_unrealized_pct = (allocated["New Unrealized $"].sum() / new_cost * 100) if new_cost else float("nan")
    kcol2.metric(
        "Unrealized %",
        f"{new_unrealized_pct:.2f}%" if pd.notna(new_unrealized_pct) else "N/A",
        delta=(
            f"{new_unrealized_pct - current_unrealized_pct:.2f} pts"
            if pd.notna(new_unrealized_pct) and pd.notna(current_unrealized_pct) else None
        ),
        help="Across your whole dividend basket -- buying more at market price dilutes this "
             "toward 0 even though your dollar unrealized gain/loss doesn't change.",
    )

    # Sum of Div Contrib % across every row -- reproduces the whole basket's blended
    # yield (see core/rebalance.py's docstring for the algebraic property). Same
    # snapshot timing as the KPI pair above.
    current_blended_yield = holdings["Current Div Contrib %"].sum()
    new_blended_yield = allocated["New Div Contrib %"].sum()
    st.metric(
        "Blended dividend yield (whole basket)", f"{new_blended_yield:.2f}%",
        delta=f"{new_blended_yield - current_blended_yield:.2f} pts",
        help="Net of 15% Thai (NRA) withholding tax. Sum of New Contrib % across every "
             "row -- shows whether this allocation raises or lowers your basket's "
             "overall yield, not just where the money is going.",
    )

    st.divider()

    # ---------- Section 3: per-stock table ----------
    st.subheader("Allocate across your dividend stocks")
    st.caption(
        "Edit % Reinvest and tick Bought? for any rows you like, then click Save changes "
        "to apply them. Bought? is just a reminder -- it doesn't insert a trade; record "
        "the real purchase yourself via Record Trade."
    )

    # st.form batches everything inside it (including the data_editor's in-progress edits)
    # and only releases current values to the script when the submit button is clicked --
    # this is what actually fixes the bug where typing a % and clicking a button OUTSIDE
    # the grid directly (without first pressing Tab/Enter or clicking another grid cell)
    # didn't reliably commit that edit. Trade-off: no more "live while typing" %
    # allocated/remaining -- forms don't rerun on keystrokes, so that's no longer possible;
    # everything now updates together on Save, which is also simpler to reason about.
    with st.form("rebalance_form"):
        edited = st.data_editor(
            snapshot["grid_source"],
            use_container_width=True,
            hide_index=True,
            disabled=[c for c in DISPLAY_COLS if c not in ("% Reinvest", "Bought?")],
            column_config={
                "History90D": st.column_config.LineChartColumn("90D Trend", width=200, help="90 Day Trend"),
                "% Reinvest": st.column_config.NumberColumn(
                    "% Reinvest", format="%.1f%%", min_value=0.0, max_value=100.0, step=1.0,
                    help="% of the $ amount above to put into this stock. Click Save changes below to apply.",
                ),
                "Invest $": st.column_config.NumberColumn("Invest $", format="$%.2f", help="$ amount above x this row's % Reinvest."),
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
                "Bought?": st.column_config.CheckboxColumn(
                    "Bought?",
                    help="Mark once you've actually bought this -- a reminder only, doesn't "
                         "insert a trade. Doesn't lock the row; % Reinvest stays editable "
                         "either way. Once every row is ticked, this plan clears and a fresh "
                         "one starts automatically.",
                ),
            },
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
