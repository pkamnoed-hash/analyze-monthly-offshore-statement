"""Pure calculation functions for the financial dashboard.

Kept free of Streamlit imports so they can be unit tested in isolation
(see tests/test_calculations.py) without needing a running app.
"""

from collections import deque

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

    return pd.DataFrame(rows, columns=["Symbol", "Trade Date", "Month", "Realized P/L"])


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
