"""Pure calculation functions for the financial dashboard.

Kept free of Streamlit imports so they can be unit tested in isolation
(see tests/test_calculations.py) without needing a running app.
"""

from collections import deque

import numpy as np
import pandas as pd


def compute_realized_pl(transactions: pd.DataFrame) -> pd.DataFrame:
    """Per-symbol realized P/L using an average-cost method over full transaction
    history. This is an estimate: it will differ slightly from the broker's
    official Realized ST/LT figures, which may use specific-lot identification."""
    tx = transactions[transactions["Symbol"].notna()].copy()
    # Corporate actions (Stock Split/ReOrg CA) take effect before market open, so on a day
    # that also has a regular trade, the split must be applied first -- otherwise its ADD
    # row overwrites (rather than compounds with) that same-day trade's quantity change.
    tx["_entry_order"] = (tx["Entry Type"] == "Trade Entry").astype(int)
    tx = tx.sort_values(["Symbol", "Trade Date", "_entry_order"])
    state = {}
    rows = []
    for _, row in tx.iterrows():
        sym = row["Symbol"]
        qty, avg_cost = state.get(sym, (0.0, 0.0))
        entry_type = row["Entry Type"]
        side = row["Side"]
        quantity = row["Quantity"] if pd.notna(row["Quantity"]) else 0.0
        price = row["Price"] if pd.notna(row["Price"]) else 0.0
        amount = row["Amount"] if pd.notna(row["Amount"]) else 0.0
        commission = row["Commission"] if pd.notna(row["Commission"]) else 0.0
        realized = 0.0

        if entry_type == "Trade Entry" and side == "buy":
            new_qty = qty + quantity
            total_cost = qty * avg_cost + quantity * price + commission
            avg_cost = total_cost / new_qty if new_qty else 0.0
            qty = new_qty
        elif entry_type == "Trade Entry" and side == "sell":
            sell_qty = -quantity
            realized = amount - avg_cost * sell_qty - commission
            qty = qty - sell_qty
        elif entry_type == "Stock Split":
            if quantity < 0:
                pass  # REMOVE row: no-op, wait for the paired ADD row
            else:
                old_qty = qty
                new_qty = quantity
                avg_cost = (old_qty * avg_cost / new_qty) if new_qty else 0.0
                qty = new_qty
        elif entry_type == "ReOrg CA":
            realized = 0.0 - avg_cost * qty
            qty = 0.0
            avg_cost = 0.0
        elif quantity:
            # Anything else with a nonzero quantity (e.g. a rights-offering distribution,
            # recorded with a blank Entry Type) -- fold it in at whatever cost this row
            # shows (typically $0 for a free distribution) instead of silently dropping
            # it, so a later sell/removal of these shares still nets out correctly rather
            # than acting on an avg_cost that never accounted for them.
            new_qty = qty + quantity
            total_cost = qty * avg_cost + quantity * price
            avg_cost = total_cost / new_qty if new_qty else 0.0
            qty = new_qty

        state[sym] = (qty, avg_cost)
        if realized != 0:
            rows.append({"Symbol": sym, "Trade Date": row["Trade Date"], "Month": row["Month"], "Realized P/L": realized})

    df = pd.DataFrame(rows, columns=["Symbol", "Trade Date", "Month", "Realized P/L"])
    # Same empty-rows dtype trap as compute_fifo_realized_pl below -- an empty `rows` list
    # would otherwise leave "Realized P/L" as dtype=object.
    df["Realized P/L"] = df["Realized P/L"].astype(float)
    return df


def _run_fifo(trades: pd.DataFrame):
    """Shared FIFO lot-tracking loop, used by both compute_fifo_realized_pl and
    compute_current_positions so the lot mechanics aren't duplicated between
    them. Returns (realized_rows, lots) where lots is the ending per-symbol
    {symbol: deque([qty, cost_per_share])} book -- i.e. the current open
    position, which compute_current_positions exposes and
    compute_fifo_realized_pl discards.

    Mirrors compute_realized_pl's same-day ordering fix (corporate actions
    processed before regular trades on the same date) and its fallback branch
    for unclassified nonzero-quantity entry types."""
    tx = trades[trades["Symbol"].notna()].copy()
    tx["_entry_order"] = (tx["Entry Type"] == "Trade Entry").astype(int)
    tx = tx.sort_values(["Symbol", "Trade Date", "_entry_order"])

    lots = {}  # symbol -> deque of [qty, cost_per_share] lots, oldest first
    rows = []

    for _, row in tx.iterrows():
        sym = row["Symbol"]
        book = lots.setdefault(sym, deque())
        entry_type = row["Entry Type"]
        side = row["Side"]
        quantity = row["Quantity"] if pd.notna(row["Quantity"]) else 0.0
        price = row["Price"] if pd.notna(row["Price"]) else 0.0
        amount = row["Amount"] if pd.notna(row["Amount"]) else 0.0
        commission = row["Commission"] if pd.notna(row["Commission"]) else 0.0
        realized = 0.0

        if entry_type == "Trade Entry" and side == "buy":
            # Commission folded into this lot's cost, matching compute_realized_pl's
            # buy handling -- consistent treatment between the two functions.
            cost_per_share = (quantity * price + commission) / quantity if quantity else 0.0
            book.append([quantity, cost_per_share])
        elif entry_type == "Trade Entry" and side == "sell":
            remaining = -quantity
            cost_removed = 0.0
            while remaining > 1e-9 and book:
                lot_qty, lot_cost = book[0]
                take = min(lot_qty, remaining)
                cost_removed += take * lot_cost
                lot_qty -= take
                remaining -= take
                if lot_qty <= 1e-9:
                    book.popleft()
                else:
                    book[0][0] = lot_qty
            realized = amount - cost_removed - commission
        elif entry_type == "Stock Split":
            if quantity < 0:
                pass  # REMOVE row: no-op, wait for the paired ADD row
            else:
                old_total = sum(q for q, _ in book)
                ratio = (quantity / old_total) if old_total else 0.0
                for lot in book:
                    lot[0] *= ratio
                    lot[1] = (lot[1] / ratio) if ratio else 0.0
        elif entry_type == "ReOrg CA":
            realized = 0.0 - sum(q * c for q, c in book)
            book.clear()
        elif quantity:
            # Same fallback as compute_realized_pl: fold in an unclassified
            # nonzero-quantity row (e.g. a rights-offering distribution) as its
            # own lot at whatever cost the row shows, instead of dropping it.
            book.append([quantity, price])

        if realized != 0:
            rows.append({
                "Symbol": sym, "Trade Date": row["Trade Date"], "Month": row["Month"],
                "Realized P/L": realized, "id": row.get("id"),
            })

    return rows, lots


def compute_fifo_realized_pl(trades: pd.DataFrame) -> pd.DataFrame:
    """FIFO-lot version of compute_realized_pl, same input/output contract
    (Symbol, Trade Date, Entry Type, Side, Quantity, Price, Amount, Commission,
    Month in; Symbol, Trade Date, Month, Realized P/L, id out -- id is the
    originating trade row's id, passed through so a realized event can be
    traced back to the exact trade that caused it). Tracks a deque of
    [qty, cost_per_share] lots per symbol instead of one running average cost,
    so a sell draws down the oldest lot(s) first rather than blending -- more
    accurate for trades logged through this app, where full lot-level history
    is available (unlike the historical average-cost estimate kept unchanged
    in compute_realized_pl -- see docs/METHODOLOGY.md)."""
    rows, _ = _run_fifo(trades)
    df = pd.DataFrame(rows, columns=["Symbol", "Trade Date", "Month", "Realized P/L", "id"])
    # id comes through row.get("id") in _run_fifo's .iterrows() loop, which boxes it as a
    # generic Python object -- left as-is, the column ends up dtype=object rather than a
    # clean numeric dtype, which pandas' merge() rejects when joined against a float64 id
    # column (seen for real merging this against dashboard.py's live_trades). float64 (not
    # int) since callers concat this with historical rows that have no id at all -> NaN.
    df["id"] = df["id"].astype(float)
    # Same object-dtype trap when `rows` is empty (e.g. only buys logged so far, nothing
    # realized yet) -- pd.DataFrame([], columns=[...]) defaults every column to dtype=object,
    # which downstream (dashboard.py's live_trades merge) turns a missing Realized P/L into
    # a literal None cell instead of NaN, and Streamlit's NumberColumn then renders that as
    # the text "None" instead of leaving it blank.
    df["Realized P/L"] = df["Realized P/L"].astype(float)
    return df


def compute_current_positions(trades: pd.DataFrame) -> pd.DataFrame:
    """Current open position per symbol, from the same FIFO lot book
    compute_fifo_realized_pl builds. Quantity is the sum of remaining lots;
    Avg Cost is their quantity-weighted average -- what you'd need to pay to
    rebuy the position, and what a future sell will use as cost. Symbols
    fully sold out (zero remaining lots) are simply absent from the result."""
    _, lots = _run_fifo(trades)
    rows = []
    for sym, book in lots.items():
        qty = sum(q for q, _ in book)
        if qty > 1e-9:
            cost = sum(q * c for q, c in book)
            rows.append({"Symbol": sym, "Quantity": qty, "Avg Cost": cost / qty, "Cost Basis": cost})
    return pd.DataFrame(rows, columns=["Symbol", "Quantity", "Avg Cost", "Cost Basis"])


def compute_holding_period_start(trades: pd.DataFrame) -> pd.Series:
    """Per-symbol: the Trade Date the currently-open position was last built up
    from zero -- i.e. the most recent point cumulative quantity crossed from ~0
    to positive and hasn't returned to ~0 since. A symbol that's been fully sold
    and later rebought resets to the rebuy date, not its original first-ever
    purchase -- this answers "how long have I continuously held what I hold
    today," not "how long ago did I first ever buy this."

    Returns a Series indexed by Symbol, values are Trade Date timestamps. Only
    symbols with a currently open position appear -- others are simply absent,
    same convention compute_current_positions() uses."""
    tx = trades[trades["Symbol"].notna() & (trades["Entry Type"] == "Trade Entry")].sort_values(["Symbol", "Trade Date"])
    starts = {}
    running = {}
    for _, row in tx.iterrows():
        sym = row["Symbol"]
        prev_qty = running.get(sym, 0.0)
        new_qty = prev_qty + row["Quantity"]
        running[sym] = new_qty
        if prev_qty <= 1e-6 and new_qty > 1e-6:
            starts[sym] = row["Trade Date"]
        if new_qty <= 1e-6:
            starts.pop(sym, None)
    return pd.Series(starts, name="Holding Period Start", dtype="object")


def estimate_sell_realized_pl(trades: pd.DataFrame, symbol: str, quantity: float, price: float):
    """Live preview for the Record Trade form: what would selling `quantity`
    shares of `symbol` at `price` realize right now, against the current FIFO
    lot book? Ignores commission (not yet known at preview time -- fee fields
    stay inside the form, only Symbol/Side/Quantity/Price are live-reactive),
    so this is an estimate, not the exact figure that gets recorded on
    submit. Returns None if there's no open position in the symbol, or if
    quantity exceeds what's currently held (can't simulate a sale against
    lots that don't exist)."""
    _, lots = _run_fifo(trades)
    book = lots.get(symbol)
    if not book:
        return None
    available = sum(q for q, _ in book)
    if quantity <= 0 or quantity > available + 1e-9:
        return None

    remaining = quantity
    cost_removed = 0.0
    for lot_qty, lot_cost in book:
        if remaining <= 1e-9:
            break
        take = min(lot_qty, remaining)
        cost_removed += take * lot_cost
        remaining -= take

    return quantity * price - cost_removed


def blended_realized_pl(xlsx_realized: pd.DataFrame, db_trades: pd.DataFrame, cutoff: pd.Timestamp) -> pd.DataFrame:
    """Combines the audited historical average-cost result (xlsx_realized =
    compute_realized_pl(xlsx_transactions)) with a FIFO recompute of the full
    trades table (db_trades = db.fetch_trades(), seed + live). Historical
    months (<= cutoff) keep their already-audited average-cost numbers;
    everything after cutoff switches to FIFO, since that's the region where
    trades were logged through this app with full lot-level detail. cutoff is
    normally the xlsx Summary sheet's max month -- the last officially
    processed statement."""
    fifo = compute_fifo_realized_pl(db_trades)
    return pd.concat([
        xlsx_realized[xlsx_realized["Trade Date"] <= cutoff],
        fifo[fifo["Trade Date"] > cutoff],
    ], ignore_index=True)


def blended_dividends(xlsx_income: pd.DataFrame, db_dividends: pd.DataFrame, cutoff: pd.Timestamp) -> pd.DataFrame:
    """Same <=cutoff/>cutoff split as blended_realized_pl, producing one
    Income-shaped frame (Symbol, Trade Date, Entry Type, Net Amt) spanning
    full history. xlsx_income is the raw xlsx Income sheet; db_dividends is
    db.fetch_dividends() (already renamed to the same column shape). The xlsx
    and db sides use different Entry Type vocabularies for the same
    real-world categories (xlsx: "Dividends"/"Div. Adj(NRA Withheld)"/
    "Credit/Margin Interest"; db: "Dividend"/"Interest"/"Capital
    Distribution") -- rather than normalize them here, callers should widen
    their Entry Type filters to recognize both, since everything else about
    the two sources already lines up."""
    cols = ["Symbol", "Trade Date", "Entry Type", "Net Amt"]
    return pd.concat([
        xlsx_income[xlsx_income["Trade Date"] <= cutoff][cols],
        db_dividends[db_dividends["Trade Date"] > cutoff][cols],
    ], ignore_index=True)


def compute_roi(investment_gain: float, capital_base: float, period_days: int):
    """ROI over a period, plus its annualized (1-year-equivalent) rate.

    capital_base is starting value + net deposits for the period — the amount
    of capital the gain is measured against. Returns (roi_pct, annualized_roi_pct);
    either element is None when it can't be meaningfully computed (no capital
    base to divide by, non-positive period, or a loss so large that compounding
    it to an annual rate would require a negative base raised to a fractional
    power).
    """
    roi_pct = (investment_gain / capital_base * 100) if capital_base > 0 else None

    annualized_roi_pct = None
    if roi_pct is not None and period_days > 0:
        years = period_days / 365.25
        if (1 + roi_pct / 100) > 0:
            annualized_roi_pct = ((1 + roi_pct / 100) ** (1 / years) - 1) * 100

    return roi_pct, annualized_roi_pct


def compute_stochastic_oscillator(high: pd.Series, low: pd.Series, close: pd.Series,
                                   k_period: int = 14, d_period: int = 3) -> dict:
    """Standard "Full Stochastic" (14, 3) oscillator: %K measures where Close sits within
    the trailing k_period High/Low range (0-100), %D is a d_period simple moving average
    of %K. Both are plain pandas rolling ops -- `.rolling(k_period)` already returns NaN
    for the first k_period-1 rows (not enough history yet), same "no data until the window
    fills" behavior every other windowed figure in this module relies on implicitly.

    A flat window (Highest High == Lowest Low -- e.g. a completely flat price run) would
    otherwise divide by zero; reads as 50 (the midpoint) instead, an arbitrary but sane
    stand-in for "no range to measure position within." Returns a dict with "%K"/"%D"
    Series, aligned to the input index."""
    highest_high = high.rolling(k_period).max()
    lowest_low = low.rolling(k_period).min()
    span = highest_high - lowest_low
    percent_k = ((close - lowest_low) / span * 100).where(span != 0, 50.0)
    percent_d = percent_k.rolling(d_period).mean()
    return {"%K": percent_k, "%D": percent_d}


def count_touches(high: pd.Series, low: pd.Series, level_price: float, tolerance_pct: float = 0.012) -> int:
    """How many bars in the given window came within `tolerance_pct` of `level_price`, or
    whose full High-Low range crossed through it -- a rough strength indicator for a
    Reference Line, distinct from the level itself (a level can be mathematically valid
    but never actually have been tested by real price action). Floored at 2 so a level
    never reads as completely untested. tolerance_pct=0.012 (1.2%) and the floor of 2 are
    first-pass values carried over from the design mockup, not independently tuned against
    real data -- a reasonable starting point, worth revisiting if it reads as too
    loose/tight in practice."""
    tolerance = max(level_price * tolerance_pct, 0.01)
    near_high = (high - level_price).abs() <= tolerance
    near_low = (low - level_price).abs() <= tolerance
    crossed = (low <= level_price) & (high >= level_price)
    return max(int((near_high | near_low | crossed).sum()), 2)


def compute_moving_average(close: pd.Series, period: int) -> pd.Series:
    """Simple moving average of Close over `period` bars -- a thin, explicit wrapper
    around `.rolling(period).mean()` rather than inlining it at each call site, so the
    Symbol Analysis page's MA 50/100/200 overlay reads as one concept in one place. NaN
    for the first period-1 rows, same convention as compute_stochastic_oscillator above."""
    return close.rolling(period).mean()


_INTERVAL_RESAMPLE_RULE = {"D": None, "W": "W", "M": "ME"}


def resample_ohlc(daily: pd.DataFrame, interval: str) -> pd.DataFrame:
    """Groups daily OHLC bars (Date/Open/High/Low/Close, the shape
    market_data.fetch_price_history returns) into weekly or monthly bars -- Interval
    (Day/Week/Month), distinct from the Symbol Analysis page's Timeline picker (how much
    history is shown). interval="D" returns `daily` unchanged (a copy, so callers can't
    accidentally mutate the caller's frame through the returned one).

    Open/High/Low/Close aggregate the standard OHLC way (first/max/min/last) via pandas'
    own `.resample()` on a DatetimeIndex -- no hand-rolled bucketing needed, unlike a
    from-scratch JS port would require. Weekly buckets end on Sunday (pandas' default "W"
    rule) -- an arbitrary but standard convention, not meant to match any particular
    trading calendar. `.dropna()` after resampling drops any bucket with no trading days
    in it (shouldn't happen for a contiguous daily series, but guards against a gap)."""
    if interval not in _INTERVAL_RESAMPLE_RULE:
        raise ValueError(f"Unknown interval {interval!r} -- expected 'D', 'W', or 'M'")
    if interval == "D":
        return daily.copy()

    indexed = daily.set_index("Date")
    resampled = indexed.resample(_INTERVAL_RESAMPLE_RULE[interval]).agg(
        {"Open": "first", "High": "max", "Low": "min", "Close": "last"}
    ).dropna()
    return resampled.reset_index()


def to_heikin_ashi(daily: pd.DataFrame) -> pd.DataFrame:
    """Heikin Ashi smoothing -- a display transform, not real traded prices (Pivot Points
    and the Stochastic oscillator must always be computed from real OHLC, never this).
    Standard recursive formula: HA-Close is the average of the real bar's own O/H/L/C;
    HA-Open is the midpoint of the *previous* HA bar's Open/Close (seeded from the real
    bar's own O/C on the very first row, since there's no previous HA bar yet); HA-High/
    Low extend the real High/Low to also include the HA Open/Close, so the smoothed
    candle body never pokes outside its own wick.

    Genuinely recursive (each row depends on the previous output row), so this is a plain
    Python loop, not a vectorized pandas op -- same shape as the mockup's JS port. Must be
    called on the FULL series before slicing to a visible window: seeding a mid-series
    row's HA-Open from a raw (non-Heikin-Ashi) previous bar would be wrong."""
    opens, highs, lows, closes = [], [], [], []
    prev_open, prev_close = None, None
    for row in daily.itertuples():
        ha_close = (row.Open + row.High + row.Low + row.Close) / 4
        ha_open = (prev_open + prev_close) / 2 if prev_open is not None else (row.Open + row.Close) / 2
        ha_high = max(row.High, ha_open, ha_close)
        ha_low = min(row.Low, ha_open, ha_close)
        opens.append(ha_open); highs.append(ha_high); lows.append(ha_low); closes.append(ha_close)
        prev_open, prev_close = ha_open, ha_close

    out = daily[["Date"]].copy()
    out["Open"] = opens
    out["High"] = highs
    out["Low"] = lows
    out["Close"] = closes
    return out


def find_swing_points(high: pd.Series, low: pd.Series, window: int = 3) -> tuple[pd.Series, pd.Series]:
    """Fractal-style local-extreme check: a bar is a "swing high" if its High is the max
    within a CENTERED window of `window` bars on each side (2*window+1 bars total,
    including the bar itself); a "swing low" the mirror image via Low/min. This is the
    standard building block traditional charting tools use to auto-draw trend lines --
    genuinely different from this page's Pivot Points levels, which are a fixed formula
    off Avg Cost, not derived from where price has actually reversed.

    Returns (is_swing_high, is_swing_low), two boolean Series aligned to the input index.
    `.rolling(..., center=True)` can't fill in the first/last `window` bars (not enough
    bars on one side yet) -- those rows come back NaN from the rolling max/min, so the
    `==` comparison against them is always False, correctly leaving the most recent
    `window` bars unconfirmed rather than guessing. A real trend line doesn't chase the
    last candle -- this is expected behavior, not a bug."""
    window_size = 2 * window + 1
    rolling_max = high.rolling(window_size, center=True).max()
    rolling_min = low.rolling(window_size, center=True).min()
    is_swing_high = high == rolling_max
    is_swing_low = low == rolling_min
    return is_swing_high, is_swing_low


def compute_swing_trend_lines(
    dates: pd.Series, high: pd.Series, low: pd.Series, window: int = 3,
    search_from: pd.Timestamp | None = None,
) -> dict:
    """Classic technical-analysis auto trend lines: connects the two most recent confirmed
    swing highs into a resistance trend line, and the two most recent confirmed swing lows
    into a support trend line -- the simple, standard version (deliberately not best-fit-
    through-many-points or violation-counting, real added complexity for a marginal
    accuracy gain). Each line is extended in a straight line through to the most recent
    bar's date, so it reads as a projected line rather than a stub stopping at the second
    swing point.

    `search_from`, if given, restricts which CONFIRMED swing points are eligible to be
    picked as one of the "two most recent" (e.g. the caller's selected Timeline cutoff) --
    but swing detection itself (`find_swing_points`) still runs over the FULL `high`/`low`
    given, not just the restricted range, same "detect with full context, then scope the
    result" rule this page's MA/Stochastic already follow (a swing point right at the edge
    of the window still needs real history on both sides to be confirmed correctly). The
    line still always extends to the last date in the FULL series (real "today"), not the
    last date within `search_from` -- Timeline scopes which swings are eligible, not how
    far forward the projection reaches.

    Returns {"resistance": [...] | None, "support": [...] | None} -- each present side is a
    list of 3 {"time": "yyyy-mm-dd", "value": float} dicts (the two swing points plus the
    extended point), ready to hand straight to the trendline_chart component as line-series
    data. A side is None when fewer than 2 eligible confirmed swing points exist (e.g. a
    short Timeline window) -- not an error, just nothing to draw yet."""
    is_swing_high, is_swing_low = find_swing_points(high, low, window)
    if search_from is not None:
        in_range = dates >= search_from
        is_swing_high = is_swing_high & in_range
        is_swing_low = is_swing_low & in_range
    swing_highs = list(zip(dates[is_swing_high], high[is_swing_high]))
    swing_lows = list(zip(dates[is_swing_low], low[is_swing_low]))

    def _extended_line(points, last_date):
        if len(points) < 2:
            return None
        (t1, p1), (t2, p2) = points[-2], points[-1]
        days_between = (t2 - t1).days
        if days_between == 0:
            return None
        slope_per_day = (p2 - p1) / days_between
        extended_price = p2 + slope_per_day * (last_date - t2).days
        return [
            {"time": t1.strftime("%Y-%m-%d"), "value": float(p1)},
            {"time": t2.strftime("%Y-%m-%d"), "value": float(p2)},
            {"time": last_date.strftime("%Y-%m-%d"), "value": float(extended_price)},
        ]

    last_date = dates.iloc[-1]
    return {
        "resistance": _extended_line(swing_highs, last_date),
        "support": _extended_line(swing_lows, last_date),
    }


def cluster_price_levels(prices, tolerance_pct: float = 0.015) -> list[dict]:
    """Groups nearby prices into clusters -- a generic building block for
    "where has price actually clustered/repeated," not specific to swing points. Sorts
    `prices` ascending, then greedily grows a cluster: each price joins the CURRENT
    (most recently opened) cluster if it's within `tolerance_pct` of that cluster's
    running average, otherwise starts a new one. Since input is processed in sorted
    order, a new price can only possibly belong to the most recent cluster -- no earlier
    cluster's average is ever closer, so a single backward comparison is sufficient
    (no need to check every existing cluster).

    Returns a list of {"price": float, "count": int} -- one entry per cluster, `price`
    the cluster's average, `count` how many input prices joined it -- sorted by `count`
    descending (the most-touched level first). Empty input returns an empty list."""
    sorted_prices = sorted(float(p) for p in prices)
    clusters = []  # list of [running_sum, count]
    for price in sorted_prices:
        if clusters:
            cluster_avg = clusters[-1][0] / clusters[-1][1]
            tolerance = max(cluster_avg * tolerance_pct, 0.01)
            if abs(price - cluster_avg) <= tolerance:
                clusters[-1][0] += price
                clusters[-1][1] += 1
                continue
        clusters.append([price, 1])

    result = [{"price": total / count, "count": count} for total, count in clusters]
    result.sort(key=lambda c: c["count"], reverse=True)
    return result


def compute_horizontal_sr_zones(
    dates: pd.Series, high: pd.Series, low: pd.Series, window: int = 3,
    search_from: pd.Timestamp | None = None, tolerance_pct: float = 0.015, max_per_side: int = 2,
) -> dict:
    """Horizontal support/resistance ZONES -- genuinely different from both Pivot Points
    (a fixed formula anchored to Cost/Sh) and compute_swing_trend_lines (a diagonal read
    on the current slope): this finds prices where the market has actually reversed
    MULTIPLE times, via the same swing-point detection plus clustering (see
    find_swing_points/cluster_price_levels above). Resistance zones cluster confirmed
    swing HIGHS; support zones cluster confirmed swing LOWS. Only clusters with 2+
    members are returned -- a single, unrepeated swing point isn't a "zone" (that
    concept is already covered by compute_swing_trend_lines), it's just noise here.
    Kept to the `max_per_side` strongest (most-touched) zones per side so the chart
    doesn't fill up with every minor cluster.

    `window`/`search_from` mean exactly what they mean in compute_swing_trend_lines --
    swing detection runs over the FULL `high`/`low` given (real context for confirmation
    near the search_from boundary), `search_from` then restricts which confirmed swings
    are eligible to be clustered. Callers should pass the SAME `window` (typically scaled
    to how many bars are in the selected range) and `search_from` they use for the trend
    line, so both overlays reflect the same swing-significance scale for a given Timeline.

    Returns {"resistance": [{"price", "count"}, ...], "support": [...]}, each list
    already sorted strongest-first and trimmed to `max_per_side` -- ready to hand
    straight to the trendline_chart component."""
    is_swing_high, is_swing_low = find_swing_points(high, low, window)
    if search_from is not None:
        in_range = dates >= search_from
        is_swing_high = is_swing_high & in_range
        is_swing_low = is_swing_low & in_range

    def _top_zones(prices):
        clusters = cluster_price_levels(prices, tolerance_pct)
        return [c for c in clusters if c["count"] >= 2][:max_per_side]

    return {
        "resistance": _top_zones(high[is_swing_high]),
        "support": _top_zones(low[is_swing_low]),
    }


def find_nearest_levels(latest_price: float, candidates) -> tuple[float | None, float | None]:
    """Given a pool of candidate price levels -- the caller decides what's in it (Pivot
    Points R/S, S/R Zones' cluster prices, whatever else), this makes no assumption about
    where they came from -- finds the nearest one ABOVE latest_price ("nearest
    resistance") and the nearest one BELOW ("nearest support"). A candidate exactly equal
    to latest_price counts as neither -- it's already been reached, not something still
    ahead to watch for.

    Returns (nearest_resistance, nearest_support), either element None if no candidate
    exists on that side (price has broken through everything, or the pool was empty)."""
    above = [c for c in candidates if c > latest_price]
    below = [c for c in candidates if c < latest_price]
    return (min(above) if above else None, max(below) if below else None)


def compute_reference_lines(
    dates: pd.Series, high: pd.Series, low: pd.Series, latest_price: float, window: int = 3,
    search_from: pd.Timestamp | None = None, max_per_side: int = 2,
) -> dict:
    """Reference Lines -- swing highs/lows selected by proximity to CURRENT price, not by
    recency (compute_swing_trend_lines' rule) or cluster strength (compute_horizontal_sr_zones'
    rule). Replaces both of those, plus the Pivot Points R/S levels, as the page's single
    line concept: resistance candidates are confirmed swing highs ABOVE latest_price, support
    candidates are confirmed swing lows BELOW latest_price -- each sorted by distance to
    latest_price and trimmed to `max_per_side`. A side with zero candidates (e.g. price at a
    new high, so no swing high sits above it) simply returns an empty list, not an error --
    there's nothing there to reference yet.

    `window`/`search_from` mean exactly what they mean in compute_swing_trend_lines/
    compute_horizontal_sr_zones -- swing detection runs over the FULL `high`/`low` given,
    `search_from` then restricts which confirmed swings are eligible to be picked. No
    minimum-spacing rule between the two picks on a side -- deliberately kept simple, plain
    closest-N by distance.

    Returns {"resistance": [price, ...], "support": [price, ...]}, each sorted
    nearest-to-latest_price-first, ready for the caller to assign ids and seed session
    state with."""
    is_swing_high, is_swing_low = find_swing_points(high, low, window)
    if search_from is not None:
        in_range = dates >= search_from
        is_swing_high = is_swing_high & in_range
        is_swing_low = is_swing_low & in_range

    resistance_candidates = [float(p) for p in high[is_swing_high] if p > latest_price]
    support_candidates = [float(p) for p in low[is_swing_low] if p < latest_price]
    resistance_candidates.sort(key=lambda p: p - latest_price)
    support_candidates.sort(key=lambda p: latest_price - p)

    return {
        "resistance": resistance_candidates[:max_per_side],
        "support": support_candidates[:max_per_side],
    }


def nearest_reference_cell(lines: list[dict], side: str, latest_price: float) -> dict:
    """Monitor Stocks' Reference Lines summary tab (v4.4.1) -- given one symbol's CAPTURED
    Reference Lines already filtered to one `side` ('resistance' or 'support', each a dict
    with at least "price" and "passed_at"), picks the nearest one (smallest price for
    resistance, largest for support) and formats it into one ready-to-display cell.

    The cell always shows the live reading, recomputed against `latest_price` every call:
    "$123.45 (+2.1%)" -- even after the line has been passed, since the % distance is
    still meaningful (it just flips sign: a passed resistance's price now reads negative,
    i.e. below current price; a passed support's now reads positive, i.e. above it). The
    "when was it passed" date is returned separately (`passed_at`, a real pd.Timestamp,
    not baked into `text`) so the caller can put it in its own sortable column -- Monitor
    Stocks' Reference Lines summary tab uses this for a "Passed R/S" column
    (st.column_config.DateColumn, same convention as the existing Ex-Date column) rather
    than a string a user couldn't sort chronologically.

    Returns {"text": "-", "passed": False, "passed_at": None} for an empty `lines` list --
    nothing captured on that side at all (e.g. price sitting at a new high, so there's no
    resistance to show)."""
    if not lines:
        return {"text": "—", "passed": False, "passed_at": None}
    nearest = (min if side == "resistance" else max)(lines, key=lambda line: line["price"])
    price = float(nearest["price"])
    pct = (price - latest_price) / latest_price * 100
    text = f"${price:,.2f} ({pct:+.1f}%)"
    passed_at = nearest.get("passed_at")
    if passed_at and pd.notna(passed_at):
        return {"text": text, "passed": True, "passed_at": pd.Timestamp(passed_at)}
    return {"text": text, "passed": False, "passed_at": None}


def apply_market_profile_fallback(live_rows: pd.DataFrame, cached_rows: pd.DataFrame) -> pd.DataFrame:
    """v4.5 -- Monitor Stocks' durable fallback for a failed live yfinance fetch
    (prompted by a real production incident: Yahoo Finance rate-limited Streamlit
    Community Cloud's shared IP right after v4.4.1 deployed, blanking every
    yfinance-derived column app-wide). `live_rows` is market_data.fetch_stock_profile()'s
    own output; `cached_rows` is db.fetch_market_profile_cache()'s durable snapshot from
    the last successful fetch of each symbol.

    A row's live fetch is considered failed when `Latest Price` is NaN -- the one field
    fetch_stock_profile()'s `except` branch always sets and its success branch never
    does (`latest_price = float(history_90d[-1])` always succeeds or the whole try
    block already raised), so it's a more reliable failure signal than `Description`
    (which a genuine success could theoretically still leave blank if yfinance's info
    dict has neither `longName` nor `shortName`).

    A failed row with a cached counterpart gets every yfinance-derived field replaced
    from the cache and `Stale=True`; a failed row with nothing ever cached stays blank,
    exactly like before this fallback existed -- no regression, just a better result
    when something WAS captured before. A row that succeeded live is returned
    untouched with `Stale=False` -- this function never prefers stale data over fresh
    data. Pure and Streamlit/DB-free by design so it's testable without a real
    fetch or a real database (see tests/test_calculations.py)."""
    cached_by_symbol = {row["Symbol"]: row for _, row in cached_rows.iterrows()} if not cached_rows.empty else {}
    fallback_cols = [
        "Description", "Sector", "Industry", "Quote Type", "Beta", "Latest Price",
        "High90D", "Low90D", "Dividend Per Year", "Dividend Yield %",
        "Dividend Frequency", "Ex-Date", "History90D",
    ]
    out_rows = []
    for _, live_row in live_rows.iterrows():
        row = live_row.to_dict()
        stale = False
        fetched_at = pd.NaT
        if pd.isna(row.get("Latest Price")):
            cached_row = cached_by_symbol.get(row["Symbol"])
            if cached_row is not None:
                for col in fallback_cols:
                    row[col] = cached_row[col]
                stale = True
                fetched_at = cached_row["Fetched At"]
        row["Stale"] = stale
        row["Fetched At"] = fetched_at
        out_rows.append(row)
    return pd.DataFrame(out_rows)


def describe_market_profile_freshness(
    fetched_at: pd.Series, symbols: pd.Series, tolerance: pd.Timedelta = pd.Timedelta(minutes=5),
) -> dict:
    """v4.5.1 -- Monitor Stocks' "Data last refreshed" caption, once profile reads
    became DB-first (each symbol can now have its own real capture time, instead of
    one shared "last refreshed" moment for the whole batch). Pure and Streamlit/DB-free
    so it's testable without a real fetch or database (see tests/test_calculations.py).

    `fetched_at`/`symbols` are same-length, same-order Series (typically two columns
    of the same DataFrame -- "Fetched At"/"Symbol" from
    db.fetch_market_profile_cache()). Returns {"newest": Timestamp, "oldest": Timestamp,
    "oldest_symbol": str, "has_variance": bool} -- `has_variance` is False whenever
    every symbol's timestamp falls within `tolerance` of the newest one, telling the
    caller not to bother naming an "oldest" outlier since there isn't a meaningful one.
    `newest`/`oldest` are still always populated even when `has_variance` is False (the
    caption's main line needs `newest` unconditionally -- e.g. "Data last refreshed:
    10/08/2026 14:22 (5 days ago)" when nobody's clicked Refresh in days but every
    symbol is uniformly stale, not just when there's a single outlier).

    `tolerance` defaults to 5 minutes (matching this app's existing "5-minute" cache
    convention elsewhere) so a real batch capture -- dozens of symbols saved moments
    apart from each other during the same Refresh or auto-capture run, genuinely a few
    seconds apart, not meaningfully stale -- doesn't read as a false "Oldest" warning.
    Caught live: an early version used a strict `!=` comparison, and a real batch of 52
    symbols (captured within the same few seconds of each other) still triggered a
    warning naming one as "the outlier" even though its timestamp was, to a human,
    indistinguishable from the rest.

    Returns {"newest": pd.NaT, "oldest": pd.NaT, "oldest_symbol": None,
    "has_variance": False} for empty input -- nothing captured yet at all (e.g. a
    genuinely fresh database, before any symbol has ever been fetched)."""
    valid = fetched_at.notna()
    if not valid.any():
        return {"newest": pd.NaT, "oldest": pd.NaT, "oldest_symbol": None, "has_variance": False}
    newest = fetched_at[valid].max()
    oldest = fetched_at[valid].min()
    oldest_symbol = symbols[valid][fetched_at[valid] == oldest].iloc[0]
    return {
        "newest": newest, "oldest": oldest, "oldest_symbol": oldest_symbol,
        "has_variance": (newest - oldest) > tolerance,
    }
