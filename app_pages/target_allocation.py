import pandas as pd
import plotly.express as px
import streamlit as st

import cached_db
from core import calculations, db, market_data, target_allocation

st.title("Target Allocation")
with st.expander("What does this page do?"):
    st.caption(
        "Compares your current holdings against targets you set at three levels -- "
        "category, sector, and stock -- and flags each one: **Over target** (more than "
        "2 points above) means sell down, **Hit target** (within 2 points either way) "
        "means hold, **Short target** (more than 2 points below) means buy more, and "
        "**Untargeted** means nobody's set a target anywhere in this row's chain yet -- "
        "not a verdict, just a reminder to configure it, shown instead of guessing "
        "against an implicit 0%. **Category** targets are a % of your whole portfolio; "
        "**Sector** targets are a % of their own Category; **Stock** targets are a % of "
        "their own Sector -- multiply down the chain to reach a stock's real, "
        "whole-portfolio target (shown as **Target %** everywhere below; the raw number "
        "you type into Sections 2/3 is **Target % of Parent**). Set targets top-down: a "
        "category with no target set makes everything under it read Untargeted too, "
        "even if you've filled in its sector/stock splits -- an explicit 0% is treated "
        "as a real decision, not the same as never setting one. Sector and stock "
        "targets don't have to sum to exactly 100% of their parent; the running totals "
        "shown below are informational, not enforced. Unlike Rebalance & Reallocate "
        "(which only decides where new cash goes), this page covers your whole "
        "portfolio and will tell you to sell."
    )


# ---------- DB-first price fetch -- same pattern as monitor_stocks.py, duplicated
# locally per this app's established per-page convention (see rebalance.py's own
# DATA_FILE/DIVIDEND_ENTRY_TYPES comment defending the same choice) rather than
# core/rebalance.py's older direct-live-call pattern (flagged in docs/ROADMAP.md as
# a known inefficiency). ----------
@st.cache_data
def _cached_read_stock_profile_from_db(symbols: list[str]) -> pd.DataFrame:
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
    live = market_data.fetch_stock_profile(symbols)
    cached = db.fetch_market_profile_cache()
    merged = calculations.apply_market_profile_fallback(live, cached)

    fresh_rows = merged[~merged["Stale"] & merged["Latest Price"].notna()]
    if not fresh_rows.empty:
        db.save_market_profile_cache(fresh_rows.drop(columns=["Stale", "Fetched At"]).to_dict("records"))

    _cached_read_stock_profile_from_db.clear()
    return int(merged["Stale"].sum())


def _pie(source_df, names_col, values_col, title):
    fig = px.pie(source_df, names=names_col, values=values_col, title=title, hole=0.4)
    fig.update_traces(textposition="inside", textinfo="percent+label")
    st.plotly_chart(fig, use_container_width=True)


# Red/amber/green for Over/Short/Hit -- same conditional-background pattern
# rebalance.py's own _styled() uses for Ex-Date, keyed off Status instead. v4.9.1 --
# "Untargeted" gets a neutral gray, deliberately NOT one of the red/amber/green judgment
# colors: it isn't a verdict on the position, it's "nobody has told the app what this
# should be yet" (see core/target_allocation.py's top docstring).
_STATUS_ROW_COLOR = {
    "Over Target": "background-color: rgba(220, 53, 69, 0.25)",
    "Short Target": "background-color: rgba(255, 193, 7, 0.25)",
    "Hit Target": "background-color: rgba(40, 167, 69, 0.20)",
    "Untargeted": "background-color: rgba(108, 117, 125, 0.15)",
}
_STATUS_MD_COLOR = {
    "Over Target": "red", "Short Target": "orange", "Hit Target": "green", "Untargeted": "gray",
}


def _styled(df):
    if "Status" not in df.columns:
        return df
    return df.style.apply(lambda row: [_STATUS_ROW_COLOR.get(row["Status"], "")] * len(row), axis=1)


def _data_freshness_caption(profile: pd.DataFrame) -> str:
    """Small reusable "as of" note, per user request repeated at each section below
    (Sector Targets/Stock Targets/Actual vs Target) rather than shown only once near
    the top of the page -- the top-level caption is easy to miss once you've scrolled
    past it, and every one of these sections displays data (sector classification,
    price-derived Actual %/Current Value) that traces back to this same profile
    fetch, so it's worth repeating exactly where that data is used, not just once."""
    if profile.empty or profile["Fetched At"].isna().all():
        return "No profile data captured yet -- click \"Refresh now\" above."
    return f"Data as of: {profile['Fetched At'].max().strftime('%d/%m/%Y %H:%M')}"


def _target_sum_caption(grid_source, group_cols, noun, *, live=False):
    """v4.9 -- running total of "Target % of Parent" in `grid_source`, grouped by
    `group_cols` (this level's own parent key: "Category" for Section 2's sector grid,
    ["Category", "Sector"] for Section 3's stock grid), compared against a flat 100% --
    "do these children sum to 100% of their shared parent" -- informational only, never
    enforced. No longer needs a `category_status`/`filter_value` lookup like before
    v4.9: the relative model always compares against a flat 100, not the parent's own
    absolute value, and `grid_source` is already scoped to whatever's on screen, so
    grouping it directly covers exactly the parents currently visible.

    `live=True` (the current design, since the Sector/Stock Targets grids run outside
    st.form) means `grid_source` is the data_editor's own live return value -- this
    updates on every committed cell edit (Tab/Enter/click away), not just after Save."""
    grouped = grid_source.groupby(group_cols)["Target % of Parent"].sum()
    lines = [
        f"{' / '.join(str(part) for part in (key if isinstance(key, tuple) else (key,)))}: "
        f"{noun} at **{total:.1f} / 100%**"
        for key, total in grouped.items()
    ]
    st.caption(" · ".join(lines) if lines else "Nothing to show for this filter yet.")
    if live:
        st.caption("*(Live -- updates as you edit. Not yet saved until you click Save changes.)*")
    else:
        st.caption("*(Reflects your last Save, not what you're typing above -- Save changes to update this.)*")


_PCT_COLUMN_CONFIG = {
    "Current Value": st.column_config.NumberColumn("Value", format="$%.2f"),
    "Actual %": st.column_config.NumberColumn("Actual Wt %", format="%.2f%%"),
    "Target % of Parent": st.column_config.NumberColumn(
        "Target % of Parent", format="%.2f%%",
        help="Raw target you set, as a share of this row's own parent (Category for a "
             "sector row, Sector for a stock row) -- not of your whole portfolio.",
    ),
    "Target %": st.column_config.NumberColumn(
        "Target Wt %", format="%.2f%%",
        help="Effective, whole-portfolio target -- Target % of Parent multiplied down "
             "through every level above it. This is the number compared against Actual %.",
    ),
    "Delta %": st.column_config.NumberColumn("Δ", format="%.2f%%"),
    "Stock Targets Sum %": st.column_config.NumberColumn(
        "Stock Targets Sum %", format="%.1f%%",
        help="Sum of this sector's own held stocks' Target % of Parent -- informational, "
             "should be ≈100% if this sector's stock splits are fully allocated, but not enforced.",
    ),
    "Trade $": st.column_config.NumberColumn(
        "Trade $", format="$%.2f",
        help="Positive = buy this many more dollars; negative = sell. Assumes your total "
             "portfolio value stays fixed, so treat this as a starting point, not an exact "
             "figure -- blank for Hit Target rows (nothing to do).",
    ),
    "Trade Shares": st.column_config.NumberColumn(
        "Trade Shares", format="%.2f", help="Trade $ / Latest Price. Positive = buy, negative = sell.",
    ),
}


if st.button("Refresh now", help="Bypass the cache and re-fetch live prices."):
    trades_now = cached_db.cached_fetch_trades()
    held_now = sorted(calculations.compute_current_positions(trades_now)["Symbol"].unique().tolist())
    stale_count = _refresh_stock_profile_live(held_now)
    if stale_count:
        st.warning(f"{stale_count} symbol(s) failed to refresh -- showing last known price.")

trades = cached_db.cached_fetch_trades()
positions = calculations.compute_current_positions(trades)
held_symbols = sorted(positions["Symbol"].unique().tolist())

if not held_symbols:
    st.info("No currently-held stocks (quantity > 0) -- nothing to track yet.")
    st.stop()

profile = _cached_read_stock_profile_from_db(held_symbols)
if not profile.empty and profile["Fetched At"].notna().any():
    st.caption(f"Market data as of: {profile['Fetched At'].max().strftime('%d/%m/%Y %H:%M')}")


@st.fragment
def _target_allocation_body(trades: pd.DataFrame, profile: pd.DataFrame):
    holdings = target_allocation.compute_actual_weights(trades, profile)

    nan_price_symbols = holdings.loc[holdings["Latest Price"].isna(), "Symbol"].tolist()
    if nan_price_symbols:
        st.warning(
            f"Price fetch failed/unavailable for: {', '.join(nan_price_symbols)} -- excluded "
            "from the total below, which slightly overstates every other symbol's Actual %."
        )

    symbol_types = cached_db.cached_fetch_symbol_types()
    target_allocations_df = cached_db.cached_fetch_target_allocations()
    target_sectors_df = cached_db.cached_fetch_target_sectors()
    target_categories_df = cached_db.cached_fetch_target_categories()

    # v4.9 -- single call runs the whole tag -> category -> sector -> stock pipeline in
    # the dependency order the relative-target model requires (each level's effective %
    # needs the one above it already computed) -- see core/target_allocation.py's own
    # docstring for the full reasoning. `holdings` above is recomputed internally here
    # too (cheap, pure pandas) -- kept as a separate call above only because the NaN
    # price warning needs it before this point.
    category_status, sector_status, stock_status = target_allocation.compute_full_target_status(
        trades, profile, symbol_types, target_categories_df, target_sectors_df, target_allocations_df,
    )
    stock_status = stock_status.rename(columns={"Classification": "Sector"})
    categories = category_status["Category"].tolist()

    stock_targets_by_sector = target_allocation.sum_stock_targets_by_sector(
        stock_status.rename(columns={"Sector": "Classification"}),
    ).rename(columns={"Target % of Parent": "Stock Targets Sum %"})
    sector_targets_by_category = target_allocation.sum_sector_targets_by_category(sector_status).rename(
        columns={"Target % of Parent": "Sector Targets Sum %"},
    )

    # ---------- Section 1: Category Targets ----------
    st.subheader("1. Category Targets")
    st.caption("Saves automatically as you type -- no Save button needed.")
    cat_cols = st.columns(len(categories))
    for col, cat in zip(cat_cols, categories):
        with col:
            current = float(category_status.loc[category_status["Category"] == cat, "Target %"].iloc[0])
            new_val = st.number_input(
                f"{cat} target %", min_value=0.0, max_value=100.0, value=current, step=0.5,
                key=f"target_alloc_cat_{cat}",
            )
            if new_val != current:
                db.set_target_category_pct(cat, new_val)
                cached_db.invalidate_target_categories()
                st.rerun(scope="fragment")
    st.caption(f"Sum of category targets: **{category_status['Target %'].sum():.1f}%**")

    st.divider()

    # ---------- Shared category filter for Sections 2 & 3 ----------
    # v4.9.2 -- one shared control instead of two independent "Filter by category"
    # radios (Section 2 and Section 3 each used to have their own, requiring the same
    # selection twice for no reason -- they always show the same category's data) --
    # per user request. Placed once here, right above both grids it scopes.
    st.markdown("**Filter by category** *(scopes both Sector Targets and Stock Targets below)*")
    shared_category_filter = st.radio(
        "Filter by category", ["All"] + categories, horizontal=True, label_visibility="collapsed",
        key="target_alloc_category_filter",
    )
    st.divider()

    # ---------- Section 2: Sector Targets ----------
    st.subheader("2. Sector Targets")
    st.caption(
        "Sectors are read automatically from your holdings' Yahoo Finance classification -- "
        "only sectors you actually hold appear here."
    )
    st.caption(_data_freshness_caption(profile))

    sector_grid_source = stock_status[["Category", "Sector"]].drop_duplicates().reset_index(drop=True)
    sector_target_map = {
        (row["Category"], row["Sector"]): row["Target %"] for _, row in target_sectors_df.iterrows()
    }
    sector_grid_source["Target % of Parent"] = [
        sector_target_map.get((row["Category"], row["Sector"]), 0.0) for _, row in sector_grid_source.iterrows()
    ]
    if shared_category_filter != "All":
        sector_grid_source = sector_grid_source[sector_grid_source["Category"] == shared_category_filter].reset_index(drop=True)

    # No st.form here, deliberately -- unlike Rebalance & Reallocate's grid (which
    # recomputes OTHER columns, e.g. New Cat Weight %, from the very cell being
    # edited, the bug that forced it into a form), this table's only column that
    # changes IS Target % of Parent itself, so nothing about its shape/identity
    # changes between reruns just from editing -- safe to let it rerun live, per
    # user request, so the running total below updates as you type instead of only
    # after Save. Trade-off: clicking Save immediately after typing, without
    # first pressing Tab/Enter/clicking another cell, can still miss that very
    # last edit -- press Tab or click elsewhere before Save if a change doesn't
    # seem to stick, same caveat Rebalance & Reallocate's own form was built to
    # avoid.
    sector_edited = st.data_editor(
        sector_grid_source, use_container_width=True, hide_index=True,
        disabled=["Category", "Sector"],
        column_config={
            "Target % of Parent": st.column_config.NumberColumn(
                "Target % of Category", format="%.1f%%", min_value=0.0, max_value=100.0, step=0.5,
                help="This sector's share of its OWN Category's target -- not of your whole "
                     "portfolio. See Section 4 for the resulting whole-portfolio (effective) %.",
            ),
        },
        key="target_allocation_sector_editor",
    )
    _target_sum_caption(sector_edited, "Category", "sector targets", live=True)
    sector_submitted = st.button("Save changes", type="primary", key="target_allocation_sector_save")

    if sector_submitted:
        previous = {(row["Category"], row["Sector"]): row["Target % of Parent"] for _, row in sector_grid_source.iterrows()}
        changed = 0
        for _, row in sector_edited.iterrows():
            key = (row["Category"], row["Sector"])
            new_pct = float(row["Target % of Parent"]) if pd.notna(row["Target % of Parent"]) else 0.0
            if new_pct != previous.get(key):
                db.set_target_sector_pct(row["Category"], row["Sector"], new_pct)
                changed += 1
        if changed:
            cached_db.invalidate_target_sectors()
            st.success(f"Updated {changed} sector target(s).")
        else:
            st.caption("No changes to save.")
        st.rerun(scope="fragment")

    st.divider()

    # ---------- Section 3: Stock Targets ----------
    st.subheader("3. Stock Targets")
    st.caption("Scoped to symbols you currently hold. A symbol with no target set defaults to 0%.")
    st.caption(f"Filtered to **{shared_category_filter}** -- change this in the filter above Section 2.")
    st.caption(_data_freshness_caption(profile))

    stock_grid_source = stock_status[["Symbol", "Category", "Sector", "Target % of Parent"]].reset_index(drop=True)
    if shared_category_filter != "All":
        stock_grid_source = stock_grid_source[stock_grid_source["Category"] == shared_category_filter].reset_index(drop=True)

    # No st.form here either -- same reasoning as the Sector Targets grid above.
    stock_edited = st.data_editor(
        stock_grid_source, use_container_width=True, hide_index=True,
        disabled=["Symbol", "Category", "Sector"],
        column_config={
            "Target % of Parent": st.column_config.NumberColumn(
                "Target % of Sector", format="%.1f%%", min_value=0.0, max_value=100.0, step=0.5,
                help="This stock's share of its OWN Sector's target -- not of your whole "
                     "portfolio. See Section 4 for the resulting whole-portfolio (effective) %.",
            ),
        },
        key="target_allocation_stock_editor",
    )
    _target_sum_caption(stock_edited, ["Category", "Sector"], "stock targets", live=True)
    stock_submitted = st.button("Save changes", type="primary", key="target_allocation_stock_save")

    if stock_submitted:
        previous = dict(zip(stock_grid_source["Symbol"], stock_grid_source["Target % of Parent"]))
        changed = 0
        for _, row in stock_edited.iterrows():
            new_pct = float(row["Target % of Parent"]) if pd.notna(row["Target % of Parent"]) else 0.0
            if new_pct != previous.get(row["Symbol"]):
                db.set_target_allocation(row["Symbol"], new_pct)
                changed += 1
        if changed:
            cached_db.invalidate_target_allocations()
            st.success(f"Updated {changed} symbol(s).")
        else:
            st.caption("No changes to save.")
        st.rerun(scope="fragment")

    st.divider()

    # ---------- Section 4: Actual vs Target ----------
    st.subheader("4. Actual vs Target")
    st.caption(_data_freshness_caption(profile))

    tabs = st.tabs(["All"] + categories)

    with tabs[0]:
        pcol1, pcol2 = st.columns(2)
        with pcol1:
            _pie(category_status, "Category", "Actual %", "Actual Weight by Category")
        with pcol2:
            _pie(category_status, "Category", "Target %", "Target Weight by Category")

        card_cols = st.columns(len(categories))
        for col, (_, row) in zip(card_cols, category_status.iterrows()):
            with col:
                with st.container(border=True):
                    st.markdown(f"**{row['Category']}**")
                    st.metric("Actual Weight %", f"{row['Actual %']:.2f}%")
                    st.caption(f"Target: {row['Target %']:.1f}% · Δ {row['Delta %']:+.2f}pp")
                    color = _STATUS_MD_COLOR.get(row["Status"], "gray")
                    st.markdown(f":{color}[**{row['Status']}**] → **{row['Action']}**")
        st.caption(f"Actual Weight % across all holdings: **{stock_status['Actual %'].sum():.2f}%** (should be ≈100%)")

    for cat, tab in zip(categories, tabs[1:]):
        with tab:
            cat_row = category_status[category_status["Category"] == cat].iloc[0]
            mcol1, mcol2, mcol3, mcol4 = st.columns(4)
            mcol1.metric("Value", f"${cat_row['Current Value']:,.2f}")
            mcol2.metric("Actual Weight %", f"{cat_row['Actual %']:.2f}%")
            mcol3.metric("Target Weight %", f"{cat_row['Target %']:.2f}%")
            with mcol4:
                color = _STATUS_MD_COLOR.get(cat_row["Status"], "gray")
                st.markdown(f":{color}[**{cat_row['Status']}**]")
                st.caption(f"→ {cat_row['Action']}")

            cat_sectors = sector_status[sector_status["Category"] == cat]
            if not cat_sectors.empty and cat_sectors["Current Value"].sum() > 0:
                pcol1, pcol2 = st.columns(2)
                with pcol1:
                    _pie(cat_sectors, "Sector", "Actual %", f"Actual Weight by Sector ({cat})")
                with pcol2:
                    _pie(cat_sectors, "Sector", "Target %", f"Target Weight by Sector ({cat})")

            st.markdown("**Sector breakdown**")
            sector_display = cat_sectors.merge(stock_targets_by_sector, on=["Category", "Sector"], how="left")
            sector_display["Stock Targets Sum %"] = sector_display["Stock Targets Sum %"].fillna(0.0)
            st.dataframe(
                _styled(sector_display[[
                    "Sector", "Current Value", "Actual %", "Target % of Parent", "Target %",
                    "Delta %", "Status", "Action", "Stock Targets Sum %",
                ]]),
                use_container_width=True, hide_index=True, column_config=_PCT_COLUMN_CONFIG,
            )

            st.markdown("**Stock detail**")
            cat_stocks = stock_status[stock_status["Category"] == cat].copy()
            # Blank Trade $/Shares for Hit Target rows -- "how much to buy" is meaningless
            # when the answer is "nothing," and a near-zero-but-not-exactly-zero number
            # there would read as false precision.
            cat_stocks.loc[cat_stocks["Status"] == "Hit Target", ["Trade $", "Trade Shares"]] = None
            st.dataframe(
                _styled(cat_stocks[[
                    "Symbol", "Sector", "Current Value", "Actual %", "Target % of Parent", "Target %",
                    "Delta %", "Status", "Action", "Trade $", "Trade Shares",
                ]]),
                use_container_width=True, hide_index=True, column_config=_PCT_COLUMN_CONFIG,
            )

            sector_sum_row = sector_targets_by_category[sector_targets_by_category["Category"] == cat]
            sector_sum_val = sector_sum_row["Sector Targets Sum %"].iloc[0] if not sector_sum_row.empty else 0.0
            st.caption(f"Sum of sector targets in {cat}: **{sector_sum_val:.1f}%** of this category (should be ≈100%)")


_target_allocation_body(trades, profile)
