"""Pure calculation functions for the financial dashboard.

Kept free of Streamlit imports so they can be unit tested in isolation
(see tests/test_calculations.py) without needing a running app.
"""

import pandas as pd


def compute_realized_pl(transactions: pd.DataFrame) -> pd.DataFrame:
    """Per-symbol realized P/L using an average-cost method over full transaction
    history. This is an estimate: it will differ slightly from the broker's
    official Realized ST/LT figures, which may use specific-lot identification."""
    tx = transactions[transactions["Symbol"].notna()].copy()
    tx = tx.sort_values(["Symbol", "Trade Date"])
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

        state[sym] = (qty, avg_cost)
        if realized != 0:
            rows.append({"Symbol": sym, "Trade Date": row["Trade Date"], "Month": row["Month"], "Realized P/L": realized})

    return pd.DataFrame(rows, columns=["Symbol", "Trade Date", "Month", "Realized P/L"])


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
