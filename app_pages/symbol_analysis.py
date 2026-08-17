from datetime import datetime

import pandas as pd
import streamlit as st

import cached_db
from app_pages.components.trendline_chart_component import trendline_chart
from core import calculations, db, market_data

# Enough calendar days to cover every Timeline option below (up to "2Y") with headroom for
# "All" to mean something more than "exactly 2Y" -- a bounded ~5-year fetch, not literally
# every day since the symbol started trading. This is a personal-portfolio tool, not a
# historical-research one; an unbounded fetch for a decades-held symbol would be needless.
HISTORY_DAYS = 1825
TIMELINE_OPTIONS = ["1M", "3M", "6M", "YTD", "1Y", "2Y", "All"]
TIMELINE_DAYS = {"1M": 30, "3M": 90, "6M": 182, "1Y": 365, "2Y": 730}  # YTD/All computed specially below
CHART_TYPES = {"Candlestick": "candle", "Heikin Ashi": "heikin"}
INTERVALS = {"Day": "D", "Week": "W", "Month": "M"}

st.title("Auto Trendline")
with st.expander("What does this page do?"):
    st.caption(
        "Auto-draws \"Reference Lines\" -- support/resistance levels picked from real "
        "swing highs and lows (where price has actually reversed), chosen by proximity "
        "to the CURRENT price rather than a fixed cost-basis formula. Up to 2 above "
        "price (resistance, red) and 2 below (support, green); a side with nothing "
        "nearby simply shows no line -- e.g. price at a new high shows no resistance. "
        "Reference Lines are CAPTURED at a moment, not recomputed every time you "
        "browse -- switching Chart type/Interval/Timeline only changes what the chart "
        "displays, it never changes your captured lines. Click \"Regenerate\" whenever "
        "you judge it's time (e.g. price already reached a line) to capture a fresh set "
        "based on whichever Timeline/Interval is currently selected. Drag any line to "
        "override it, click its × to remove it, or \"+ Add Reference Line\" to place a "
        "new one by hand. Cost/Sh (your real average cost) and Latest Price are shown "
        "separately as plain, non-draggable facts, not reference lines."
    )

# Avg Cost/Quantity come from live trade history (cached + invalidated on write, see
# cached_db.py -- a trade just recorded still shows up immediately, it just no longer
# costs a real Turso round trip on every plain navigation), not from the market-data
# fetch below.
positions = calculations.compute_current_positions(cached_db.cached_fetch_trades())
symbol_types = cached_db.cached_fetch_symbol_types()

query_symbol = st.query_params.get("symbol")

# ---------------------------------------------------------------------------------
# Zone 1: category filter -> symbol picker, skipped entirely when reached via
# Monitor Stocks' "view" cell (query_symbol already pre-selects one) -- same
# category vocabulary and radio-filter pattern as Monitor Stocks' own type_filter.
# ---------------------------------------------------------------------------------
if not query_symbol:
    available = positions.merge(symbol_types, on="Symbol", how="left")
    available["Allocation Type"] = available["Allocation Type"].fillna("Others")
    if available.empty:
        st.info("No current holdings to analyze.")
        st.stop()

    category = st.radio(
        "Filter by type", ["All", "Others", "Dividend", "Growth"],
        horizontal=True, label_visibility="collapsed", key="symbol_analysis_category_filter",
    )
    symbol_options = available if category == "All" else available[available["Allocation Type"] == category]
    if symbol_options.empty:
        st.info(f"No current holdings in the {category} category.")
        st.stop()
    symbol = st.selectbox(
        "Symbol", sorted(symbol_options["Symbol"].tolist()),
        help="Or click \"view\" on a row in Monitor Stocks' Reference Lines tab to jump straight here.",
    )
else:
    symbol = query_symbol
symbol = symbol.upper()


@st.cache_data(ttl=300)
def _cached_price_history(sym: str) -> tuple[pd.DataFrame, datetime]:
    return market_data.fetch_price_history(sym, HISTORY_DAYS), datetime.now()


daily_history, last_refreshed = _cached_price_history(symbol)
if daily_history.empty:
    st.warning(f"Couldn't fetch price history for {symbol}.")
    st.stop()

position_row = positions[positions["Symbol"] == symbol]
if position_row.empty:
    st.info(f"{symbol} isn't a current holding -- Reference Lines need a live position to anchor P/L to.")
    st.stop()
avg_cost = float(position_row.iloc[0]["Avg Cost"])
shares = float(position_row.iloc[0]["Quantity"])

st.subheader(symbol)
latest_price = float(daily_history["Close"].iloc[-1])
unrealized_dollar = shares * (latest_price - avg_cost)
unrealized_pct = (latest_price - avg_cost) / avg_cost * 100 if avg_cost else float("nan")

# Same delta_color="off" convention Monitor Stocks' own Unrealized % KPI already uses --
# each metric's delta is the OTHER figure (dollar vs. percent), shown without a green/red
# arrow since it's a cross-reference, not a change-over-time.
stat1, stat2, stat3, stat4, stat5 = st.columns(5)
stat1.metric("Latest Price", f"${latest_price:,.2f}")
stat2.metric("Cost/Sh", f"${avg_cost:,.4f}")
stat3.metric("Shares", f"{shares:,.4f}")
stat4.metric("Unrealized $", f"${unrealized_dollar:,.2f}", delta=f"{unrealized_pct:+.2f}%", delta_color="off")
stat5.metric("Unrealized %", f"{unrealized_pct:+.2f}%", delta=f"${unrealized_dollar:,.2f}", delta_color="off")

st.caption(f"Price data last refreshed: {last_refreshed.strftime('%d/%m/%Y %H:%M')}")

# ---------------------------------------------------------------------------------
# Zone 2 through 5 all live inside one @st.fragment. Chart type/Interval/Timeline sit
# in the SAME toolbar row as the Cost/Sh/Reference Lines/Lock/Indicators controls (one compact row,
# TradingView-style, instead of stacked blocks) -- Streamlit only allows that if
# everything sharing the row lives in a single fragment: fragments are explicitly
# barred from rendering widgets into containers created outside themselves (confirmed
# against Streamlit's own runtime/fragment.py: "Fragments can't render widgets to
# externally created containers"). The tradeoff: candles/MA/Stochastic/touches --
# previously computed once, outside any fragment -- now recompute on every fragment
# rerun, including e.g. a Lock toggle that doesn't actually need them. That's
# acceptable here: it's local pandas/numpy work on already-cached daily_history (no
# network call), a few milliseconds at most -- not the DB write's real network-latency
# cost, which stays exactly as guarded as before (see the snapshot check below).
#
# The two-rerun-per-drag pattern inside stays -- it isn't just inefficiency, it's load-
# bearing for correctness. The chart's returned drag result is only visible to Python
# AFTER trendline_chart() is called, but the chart's own NEXT render needs
# session_state already updated BEFORE that call runs, earlier in this same script
# pass. Without the explicit rerun, the first pass would redraw the chart with the
# pre-drag levels -- since Streamlit cancels/replaces an in-flight render the instant a
# new rerun is triggered, that stale frame is never actually painted, so there's no
# visible snap-back. What the fragment fixes is the SCOPE of every interaction in here
# (toolbar, checkboxes, sell %, regenerate, add, drag, delete), not the rerun count for
# a drag specifically.
@st.fragment
def _render_symbol_analysis_zones():
    # ---------------------------------------------------------------------------
    # Zone 2 + Cost/Sh/Reference Lines/Lock/Indicators -- one row. Chart type, Interval
    # (what one bar represents), and Timeline (how much history is shown) match
    # TradingView's own vocabulary. Indicators stays a popover (a button that opens a
    # small panel) -- real added complexity its 3 MAs don't need spelled out inline.
    # Cost/Sh, Reference Lines, and Lock are each their own toggle, not nested inside a
    # popover, since they're single on/off states the user wants to flip at a glance,
    # not a group of display preferences. (The old "Levels" popover that used to hold
    # Cost/Sh alongside a separate "Show Latest Price" checkbox was removed -- the
    # latest-price line is a fact the user always wants visible, same as the "Latest
    # Price" stat already shown above the chart, so it's hardcoded on in the
    # trendline_chart() call below instead of being a togglable option.) Chart type/
    # Interval/Timeline keys are NOT per-symbol (unlike the rest of this page's state)
    # -- these read as viewing preferences that should carry over when you switch
    # symbols, matching their behavior before this row was reorganized. Note: none of
    # these three controls the captured Reference Line set itself anymore (see the
    # capture/regenerate model below) -- they only ever affect what the chart displays.
    # ---------------------------------------------------------------------------
    col1, col2, col3, col4, col5, col6, col7 = st.columns([1.1, 1, 1.8, 0.9, 1.1, 0.9, 1.0])
    with col1:
        chart_type_label = st.radio("Chart type", list(CHART_TYPES.keys()), horizontal=True, key="symbol_analysis_chart_type")
        chart_type = CHART_TYPES[chart_type_label]
    with col2:
        interval_label = st.radio("Interval", list(INTERVALS.keys()), horizontal=True, key="symbol_analysis_interval")
        interval = INTERVALS[interval_label]
    with col3:
        timeline = st.radio(
            "Timeline", TIMELINE_OPTIONS, horizontal=True, index=TIMELINE_OPTIONS.index("YTD"),
            key="symbol_analysis_timeline",
        )
    with col4:
        show_pivot = st.toggle("Cost/Sh", value=True, key=f"show_pivot_{symbol}")
    with col5:
        show_reference_lines = st.toggle("Reference Lines", value=True, key=f"show_reference_lines_{symbol}")
    with col6:
        lock_rs = st.toggle("Lock", value=False, key=f"lock_rs_{symbol}")
    with col7:
        with st.popover("Indicators"):
            show_ma50 = st.checkbox("MA 50", value=False, key=f"show_ma50_{symbol}")
            show_ma100 = st.checkbox("MA 100", value=True, key=f"show_ma100_{symbol}")
            show_ma200 = st.checkbox("MA 200", value=False, key=f"show_ma200_{symbol}")

    # Timeline slices the already-fetched daily history down to a date range; Interval
    # and Heikin Ashi are applied to the FULL fetched series first, then sliced the
    # same way -- both are display transforms that need real history behind the left
    # edge of the window to render correctly there (Heikin Ashi's Open is recursive; a
    # weekly/monthly bucket at the very start of the window still needs its full real
    # days to aggregate correctly).
    today = pd.Timestamp.today().normalize()
    if timeline == "YTD":
        cutoff = pd.Timestamp(year=today.year, month=1, day=1)
    elif timeline == "All":
        cutoff = daily_history["Date"].min()
    else:
        cutoff = today - pd.Timedelta(days=TIMELINE_DAYS[timeline])

    # Real daily OHLC over the selected Timeline window -- used for touch counts below
    # (how many real candles came near a captured Reference Line's price), independent
    # of Interval/Chart type, same convention this used to serve for Pivot Points.
    chart_df = daily_history[daily_history["Date"] >= cutoff].reset_index(drop=True)
    if chart_df.empty:
        chart_df = daily_history  # cutoff landed after all available data (e.g. a very new position)

    resampled_full = calculations.resample_ohlc(daily_history, interval)
    display_source = calculations.to_heikin_ashi(resampled_full) if chart_type == "heikin" else resampled_full
    display_df = display_source[display_source["Date"] >= cutoff].reset_index(drop=True)
    if display_df.empty:
        display_df = display_source

    # MA 50/100/200 and the Stochastic oscillator are always computed from REAL
    # Close/OHLC, resampled to the selected Interval but never Heikin Ashi-smoothed.
    # Computed over the FULL resampled series first, then sliced to the Timeline
    # window -- an MA 200/a 14-period %K needs real history behind the left edge of
    # the visible window to be correct there, same "transform before slicing" rule
    # Heikin Ashi itself follows.
    ma_full = pd.DataFrame({"Date": resampled_full["Date"]})
    for period in (50, 100, 200):
        ma_full[f"ma{period}"] = calculations.compute_moving_average(resampled_full["Close"], period)
    ma_display = ma_full[ma_full["Date"] >= cutoff].reset_index(drop=True)
    if ma_display.empty:
        ma_display = ma_full

    stoch = calculations.compute_stochastic_oscillator(
        resampled_full["High"], resampled_full["Low"], resampled_full["Close"],
    )
    stoch_full = pd.DataFrame({"Date": resampled_full["Date"], "K": stoch["%K"], "D": stoch["%D"]})
    stoch_display = stoch_full[stoch_full["Date"] >= cutoff].reset_index(drop=True)
    if stoch_display.empty:
        stoch_display = stoch_full

    # Reference Lines candidate pool -- swing highs/lows nearest to CURRENT price (see
    # core.calculations.compute_reference_lines). Real High/Low, resampled to the
    # selected Interval but never Heikin Ashi-smoothed (same rule as MA/Stochastic).
    # Swing DETECTION runs over the FULL resampled series (a swing right at the edge
    # of the Timeline window still needs real history on both sides to be confirmed
    # correctly); search_from=cutoff then restricts which confirmed swings are
    # eligible to be picked. This is only the AUTO-COMPUTED candidate set for a
    # "Regenerate" click -- it does NOT drive what's currently drawn on the chart;
    # see the capture/regenerate state model below for that.
    #
    # The swing WINDOW scales with how many bars are actually in the selected
    # Timeline -- a fixed small window only ever finds short-term squiggles. ~1
    # candidate swing per 25 bars is a first-pass heuristic, same spirit as this
    # page's other heuristics (e.g. count_touches' tolerance).
    bars_in_range = int((resampled_full["Date"] >= cutoff).sum())
    swing_window = min(25, max(3, bars_in_range // 25))
    auto_reflines = calculations.compute_reference_lines(
        resampled_full["Date"], resampled_full["High"], resampled_full["Low"],
        latest_price, window=swing_window, search_from=cutoff,
    )

    ma_series_payload = {
        period: [
            {"time": row["Date"].strftime("%Y-%m-%d"), "value": float(row[f"ma{period}"])}
            for _, row in ma_display.iterrows() if pd.notna(row[f"ma{period}"])
        ]
        for period in (50, 100, 200)
    }
    ma_visible_payload = {50: show_ma50, 100: show_ma100, 200: show_ma200}

    stochastic_payload = {
        "k": [
            {"time": row["Date"].strftime("%Y-%m-%d"), "value": float(row["K"])}
            for _, row in stoch_display.iterrows() if pd.notna(row["K"])
        ],
        "d": [
            {"time": row["Date"].strftime("%Y-%m-%d"), "value": float(row["D"])}
            for _, row in stoch_display.iterrows() if pd.notna(row["D"])
        ],
    }

    candles = [
        {"time": row.Date.strftime("%Y-%m-%d"), "open": row.Open, "high": row.High, "low": row.Low, "close": row.Close}
        for row in display_df.itertuples()
    ]

    # ---------------------------------------------------------------------------
    # Reference Lines state -- ONE captured set per symbol (NOT per Timeline/Interval
    # -- revised after real discussion: keying by Timeline/Interval meant simply
    # browsing to a different Timeline silently swapped in a whole new set, fighting
    # the actual point of a captured watch-list). Switching Chart type/Interval/
    # Timeline above changes what the chart displays; it never touches this state.
    # A single "Regenerate" click re-runs compute_reference_lines() against whatever
    # Timeline/Interval is selected *at that moment* and overwrites the whole set --
    # that's the deliberate "capture" action. First-ever view of a symbol (nothing
    # captured yet, in session state or the DB) auto-runs Regenerate once so the
    # chart isn't empty; every change after that only happens on an explicit click.
    # ---------------------------------------------------------------------------
    refline_state_key = f"refline_state_{symbol}"
    refline_next_id_key = f"refline_next_id_{symbol}"
    refline_basis_key = f"refline_basis_{symbol}"

    def _regenerate_reference_lines():
        next_id = st.session_state.get(refline_next_id_key, 0)
        lines = []
        for price in auto_reflines["resistance"] + auto_reflines["support"]:
            lines.append({"id": next_id, "price": price, "is_override": False})
            next_id += 1
        st.session_state[refline_state_key] = lines
        st.session_state[refline_next_id_key] = next_id
        st.session_state[refline_basis_key] = {
            "timeline": timeline, "interval": interval_label,
            "captured_at": pd.Timestamp.today().strftime("%d/%m/%Y"),
        }

    if refline_state_key not in st.session_state:
        saved = db.fetch_reference_lines()
        saved_for_symbol = saved[saved["Symbol"] == symbol] if not saved.empty else saved
        if not saved_for_symbol.empty:
            lines = [
                {"id": i, "price": float(row["Price"]), "is_override": bool(row["Is Override"])}
                for i, (_, row) in enumerate(saved_for_symbol.iterrows())
            ]
            st.session_state[refline_state_key] = lines
            st.session_state[refline_next_id_key] = len(lines)
            # Real bug found while adding the "Passed R/S" column below: without seeding
            # this here, a completely fresh session hydrating UNCHANGED lines straight
            # from the DB still fails the DB-persistence block's snapshot check further
            # down (nothing has been recorded as "already saved" yet in THIS session),
            # triggering a needless resave that silently wipes every line's passed_at --
            # merely opening this page for a symbol that had a real frozen "passed" alert
            # erased it. Seeding the snapshot here to match what was just loaded makes
            # that guard correctly recognize "nothing changed" on first render too.
            st.session_state[f"refline_last_saved_{symbol}"] = tuple(
                sorted((round(line["price"], 6), line["is_override"]) for line in lines)
            )
            first_row = saved_for_symbol.iloc[0]
            st.session_state[refline_basis_key] = {
                "timeline": first_row["Captured Timeline"] if pd.notna(first_row["Captured Timeline"]) else "—",
                "interval": first_row["Captured Interval"] if pd.notna(first_row["Captured Interval"]) else "—",
                "captured_at": pd.Timestamp(first_row["Updated At"]).strftime("%d/%m/%Y") if pd.notna(first_row["Updated At"]) else "—",
            }
        else:
            _regenerate_reference_lines()

    sell_pct_key = f"sell_pct_{symbol}"
    sell_pct_gen_key = f"sell_pct_gen_{symbol}"
    if sell_pct_key not in st.session_state:
        st.session_state[sell_pct_key] = 100
    if sell_pct_gen_key not in st.session_state:
        st.session_state[sell_pct_gen_key] = 0

    # Regenerate/+Add act on the captured line DATA -- kept as their own small row,
    # same spirit as the old Reset/Restore row this replaces.
    btn1, btn2 = st.columns(2)
    if btn1.button("Regenerate", help="Capture a fresh set from the current Timeline/Interval, discarding any drags/deletes/manual adds."):
        _regenerate_reference_lines()
        st.rerun(scope="fragment")
    if btn2.button("+ Add Reference Line"):
        next_id = st.session_state.get(refline_next_id_key, 0)
        st.session_state[refline_state_key].append(
            {"id": next_id, "price": latest_price * 1.02, "is_override": True}
        )
        st.session_state[refline_next_id_key] = next_id + 1
        st.rerun(scope="fragment")
    basis = st.session_state.get(refline_basis_key) or {}
    st.caption(
        f"Captured from {basis.get('timeline', '—')} / {basis.get('interval', '—')} "
        f"on {basis.get('captured_at', '—')}"
    )

    # The component only ever draws a candlestick series -- "Line" was removed as a
    # selectable Chart type, and Heikin Ashi is already baked into display_df by Python
    # above, not a separate case the component needs to know about.
    result = trendline_chart(
        candles,
        [{"id": line["id"], "price": line["price"]} for line in st.session_state[refline_state_key]],
        show_reference_lines=show_reference_lines,
        cost_per_share=avg_cost,
        latest_price=latest_price,
        ma_series=ma_series_payload,
        ma_visible=ma_visible_payload,
        chart_type="candle",
        # "latest" hardcoded True -- the "Show Latest Price" checkbox was removed
        # (redundant with the always-visible "Latest Price" stat above); "pivot" still
        # follows the "Cost/Sh" toggle (col4 above).
        visibility={"pivot": show_pivot, "latest": True},
        locked=lock_rs,
        stochastic=stochastic_payload,
        key=f"trendline_chart_{symbol}",
    )

    # The component's return value persists across reruns until the JS side calls
    # setComponentValue again -- both branches below guard against re-applying the same
    # already-applied action on an unrelated rerun (e.g. toggling a checkbox). `id` comes
    # back from JS as a string (JS object keys are always strings) regardless of the int
    # id Python originally sent, so comparisons go through str() on both sides.
    if result is not None:
        lines = st.session_state[refline_state_key]
        if result.get("action") == "drag":
            target_id, new_price = str(result.get("id")), result.get("price")
            for line in lines:
                if str(line["id"]) == target_id:
                    if line["price"] != new_price:
                        line["price"] = new_price
                        line["is_override"] = True
                        st.rerun(scope="fragment")
                    break
        elif result.get("action") == "delete":
            target_id = str(result.get("id"))
            new_lines = [line for line in lines if str(line["id"]) != target_id]
            if len(new_lines) != len(lines):
                st.session_state[refline_state_key] = new_lines
                st.rerun(scope="fragment")

    # -------------------------------------------------------------------------------
    # DB persistence (Point 3/Point 9 -- "just prepare S/R to notify me in the
    # future"): every meaningful change to the captured Reference Line set is upserted
    # (delete-then-insert-all, scoped to this symbol) into reference_lines. Guarded by
    # a session-only snapshot so unrelated reruns (toggling a checkbox, or switching
    # Chart type/Interval/Timeline -- which no longer touches the captured set at all)
    # don't re-write identical values on every interaction.
    # -------------------------------------------------------------------------------
    save_snapshot_key = f"refline_last_saved_{symbol}"
    current_lines = st.session_state[refline_state_key]
    snapshot = tuple(sorted((round(line["price"], 6), line["is_override"]) for line in current_lines))
    if st.session_state.get(save_snapshot_key) != snapshot:
        db.save_reference_lines(
            symbol,
            [{"price": line["price"], "is_override": line["is_override"]} for line in current_lines],
            latest_price=latest_price,
            captured_timeline=basis.get("timeline"),
            captured_interval=basis.get("interval"),
        )
        st.session_state[save_snapshot_key] = snapshot
        # Bust Monitor Stocks' own 5-minute-cached summary tab -- without this, a
        # Regenerate/drag/delete/add here (which resets passed_at to NULL right above)
        # stayed invisible on Monitor Stocks until that cache's TTL happened to expire on
        # its own, showing a stale highlighted/"Passed R/S" cell for up to 5 minutes after
        # a fresh capture that had already cleared the underlying DB row. Real bug, caught
        # live: regenerating DVYE here still showed it passed on Monitor Stocks right after.
        cached_db.invalidate_reference_line_summary()

    # v4.4.1 tweak -- same passed-detection Monitor Stocks' cached summary tab uses
    # (core.db.mark_reference_lines_passed), run here too so a symbol viewed directly
    # (never through Monitor Stocks' own 5-minute-cached batch check) still gets an
    # up-to-date "Passed R/S" reading in Zone 5 below, not a stale one. Runs
    # unconditionally (not just inside the save guard above) since price can move
    # even when the captured line set itself hasn't changed.
    db.mark_reference_lines_passed({symbol: latest_price})

    # -------------------------------------------------------------------------------
    # Zone 4: Stochastic oscillator -- rendered inside the trendline_chart component
    # itself (a second, synced lightweight-charts pane), not a separate call here.
    # -------------------------------------------------------------------------------

    # -------------------------------------------------------------------------------
    # Zone 5: Level/Price/Total P/L/% table, live watch-highlight, and the "% to
    # sell" simulator. Total P/L is "if price reached this line and I sold sell_pct%
    # of my shares there" -- scales with the simulator; % is per-share and doesn't.
    # Resistance/support is derived live from price vs. latest_price (same rule the
    # chart itself uses), so the row nearest on each side is highlighted -- the one
    # you'd actually watch next -- rather than trying to detect a line "being
    # reached" (definitionally can't happen once side flips live the instant price
    # crosses it; that's a deliberate design point, not a gap).
    # -------------------------------------------------------------------------------
    st.subheader("Reference Lines vs. Cost/Sh")

    sell_col1, sell_col2 = st.columns([4, 1])
    with sell_col1:
        slider_val = st.slider(
            "% to sell", 0, 100, int(st.session_state[sell_pct_key]),
            key=f"{sell_pct_key}_slider_{st.session_state[sell_pct_gen_key]}",
        )
    with sell_col2:
        num_val = st.number_input(
            "% to sell", 0, 100, int(st.session_state[sell_pct_key]),
            key=f"{sell_pct_key}_num_{st.session_state[sell_pct_gen_key]}", label_visibility="collapsed",
        )
    if slider_val != st.session_state[sell_pct_key]:
        st.session_state[sell_pct_key] = slider_val
        st.session_state[sell_pct_gen_key] += 1
        st.rerun(scope="fragment")
    elif num_val != st.session_state[sell_pct_key]:
        st.session_state[sell_pct_key] = num_val
        st.session_state[sell_pct_gen_key] += 1
        st.rerun(scope="fragment")

    sell_pct = st.session_state[sell_pct_key]
    shares_to_sell = shares * sell_pct / 100
    st.caption(f"= {shares_to_sell:,.4f} of {shares:,.4f} shares")

    # "Passed R/S" per line -- looked up from the DB by price (rounded the same way the
    # snapshot-equality check above does), not recomputed here: mark_reference_lines_passed
    # above just ran against this line's own captured_side/passed_at record, the same
    # source Monitor Stocks' summary tab reads, so the value shown here matches it exactly.
    saved_reflines = db.fetch_reference_lines()
    saved_for_symbol = saved_reflines[saved_reflines["Symbol"] == symbol] if not saved_reflines.empty else saved_reflines
    passed_at_by_price = {
        round(float(row["Price"]), 6): row["Passed At"] for _, row in saved_for_symbol.iterrows()
    } if not saved_for_symbol.empty else {}

    sorted_lines = sorted(current_lines, key=lambda line: line["price"], reverse=True)
    levels_rows = []
    is_resistance_ordered = []  # parallel list, same order/index as levels_rows -- avoids a
                                 # hidden helper column, since Styler.hide()'s exact
                                 # column-subset support varies across pandas versions
                                 # (same reasoning this page's earlier highlight function
                                 # already used, before Reference Lines replaced it).
    for line in sorted_lines:
        price = line["price"]
        is_resistance = price > latest_price
        touch_count = calculations.count_touches(chart_df["High"], chart_df["Low"], price)
        label = f"{'Resistance' if is_resistance else 'Support'} ({touch_count} touches)"
        total_pl = shares * (sell_pct / 100) * (price - avg_cost)
        pct_pl = (price - avg_cost) / avg_cost * 100 if avg_cost else float("nan")
        passed_at = passed_at_by_price.get(round(price, 6))
        levels_rows.append({
            "Level": label, "Price": price, "Total P/L": total_pl, "%": pct_pl,
            "Passed R/S": pd.Timestamp(passed_at) if passed_at and pd.notna(passed_at) else pd.NaT,
        })
        is_resistance_ordered.append(is_resistance)
    levels_table = pd.DataFrame(levels_rows)

    resistance_prices = [line["price"] for line in current_lines if line["price"] > latest_price]
    support_prices = [line["price"] for line in current_lines if line["price"] < latest_price]
    nearest_resistance = min(resistance_prices) if resistance_prices else None
    nearest_support = max(support_prices) if support_prices else None

    def _highlight_nearest(row: pd.Series) -> list[str]:
        # Highlights the single nearest line on each side -- "the one to watch right
        # now" -- not every row on that side, and not a "reached" state (see comment
        # above on why that's structurally impossible under live-derived sides).
        is_resistance = is_resistance_ordered[row.name]
        if is_resistance and nearest_resistance is not None and row["Price"] == nearest_resistance:
            return ["background-color: rgba(239, 83, 80, 0.28)"] * len(row)
        if not is_resistance and nearest_support is not None and row["Price"] == nearest_support:
            return ["background-color: rgba(38, 166, 154, 0.28)"] * len(row)
        return [""] * len(row)

    if levels_table.empty:
        st.caption("No Reference Lines captured -- click \"Regenerate\" above, or \"+ Add Reference Line\" to place one by hand.")
    else:
        styler = levels_table.style.apply(_highlight_nearest, axis=1).format(
            {
                "Price": "${:,.2f}", "Total P/L": "${:+,.2f}", "%": "{:+.2f}%",
                "Passed R/S": lambda v: v.strftime("%d/%m/%Y") if pd.notna(v) else "—",
            },
        )
        st.dataframe(styler, use_container_width=True, hide_index=True)

    if nearest_resistance is not None and nearest_support is not None:
        st.caption(f"Nearest reference line above: **${nearest_resistance:,.2f}**  ·  below: **${nearest_support:,.2f}**")
    elif nearest_resistance is not None:
        st.caption(f"Nearest reference line above: **${nearest_resistance:,.2f}**  ·  no captured line below current price.")
    elif nearest_support is not None:
        st.caption(f"No captured line above current price  ·  nearest reference line below: **${nearest_support:,.2f}**")


_render_symbol_analysis_zones()
