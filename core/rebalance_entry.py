"""V4.20 -- reads, formats, previews and applies edits to the Rebalance &
Reallocate plan (rebalance_plans/rebalance_plan_items), for Rich's
get_rebalance_plan/update_rebalance_plan/reset_rebalance_plan MCP tools. Same
shape as core/trade_entry.py: pure functions, no Streamlit import, DB access
only through an injectable `conn=` kwarg.

This never records a real trade or dividend -- Bought? on the plan is a
reminder only, same as on the web page (app_pages/rebalance.py). The actual
purchase still goes through Record Trade / record_trades_batch.
"""

import pandas as pd

from core import db, rebalance


def get_plan_with_holdings(*, conn=None) -> dict:
    """{"holdings": DataFrame, "plan": dict}. `holdings` is
    rebalance.get_dividend_holdings() with a placeholder "Dividends
    Received" = 0.0 column added -- needed only so apply_allocation() has
    every column it expects. This MCP surface reports Cat Weight/Div
    Contrib/Expected Div/Mo, not the xlsx-blended actual-dividends figures
    the web page shows (Total P/L, Total P/L %) -- computing those would mean
    loading the offshore-statement workbook in a process that otherwise
    never touches it, so they're left out here rather than faked.

    Auto-starts a blank plan via db.start_rebalance_plan() if none is active
    yet -- mirrors app_pages/rebalance.py's own "no active plan" handling."""
    profile = db.fetch_market_profile_cache(conn=conn)
    holdings = rebalance.get_dividend_holdings(profile, conn=conn)
    if not holdings.empty:
        holdings = holdings.copy()
        holdings["Dividends Received"] = 0.0

    plan = db.get_active_rebalance_plan(conn=conn)
    if plan is None:
        db.start_rebalance_plan(holdings["Symbol"].tolist() if not holdings.empty else [], conn=conn)
        plan = db.get_active_rebalance_plan(conn=conn)
    return {"holdings": holdings, "plan": plan}


def describe_plan(holdings: pd.DataFrame, plan: dict) -> str:
    """Pure text formatter (no DB access): $ amount, % allocated/remaining,
    one line per symbol (% Reinvest/Invest $/Bought status), then the
    blended Div Contrib %/Expected Div/Mo current vs. after this allocation.
    `holdings` must already carry the "Dividends Received" placeholder
    get_plan_with_holdings() adds."""
    if holdings.empty:
        return "No Dividend-classified holdings currently held."

    pct_by_symbol = {s: plan["items"].get(s, {}).get("pct", 0.0) for s in holdings["Symbol"]}
    bought_by_symbol = {s: plan["items"].get(s, {}).get("bought", False) for s in holdings["Symbol"]}
    allocated = rebalance.apply_allocation(holdings, plan["amount"], pct_by_symbol)

    allocated_pct = sum(pct_by_symbol.values())
    remaining_pct = 100.0 - allocated_pct

    lines = [f"${plan['amount']:,.2f} to invest -- {allocated_pct:.1f}% allocated, {remaining_pct:.1f}% remaining."]
    for _, row in allocated.sort_values("Invest $", ascending=False).iterrows():
        symbol = row["Symbol"]
        status = "bought" if bought_by_symbol.get(symbol, False) else "not bought"
        lines.append(f"  - {symbol}: {pct_by_symbol.get(symbol, 0.0):.1f}% (${row['Invest $']:,.2f}) [{status}]")

    current_div_mo = holdings["Current Expected Div/Mo"].sum()
    new_div_mo = allocated["New Expected Div/Mo"].sum()
    current_contrib = holdings["Current Div Contrib %"].sum()
    new_contrib = allocated["New Div Contrib %"].sum()
    lines.append(
        f"Expected Div/Mo: ${current_div_mo:,.2f} -> ${new_div_mo:,.2f}  |  "
        f"Div Contrib %: {current_contrib:.2f}% -> {new_contrib:.2f}%"
    )
    return "\n".join(lines)


def validate_rebalance_edits(items: list[dict], held_symbols: set) -> tuple[list[dict], list[str]]:
    """Per-item validation. A malformed item is reported back, never silently
    dropped or silently ignored -- same convention trade_entry.validate_trade_rows
    established. `symbol` must already be a current Dividend-classified holding
    (the only ones a plan has rows for); `pct` (if given) must be in [0, 100];
    `bought` (if given) must be a real bool."""
    valid, errors = [], []
    for i, item in enumerate(items):
        try:
            symbol = item.get("symbol")
            if symbol not in held_symbols:
                raise ValueError(f"symbol {symbol!r} isn't a current Dividend-classified holding")

            entry = {"symbol": symbol}
            if item.get("pct") is not None:
                pct = float(item["pct"])
                if pd.isna(pct) or pct < 0 or pct > 100:
                    raise ValueError(f"pct must be between 0 and 100, got {item['pct']!r}")
                entry["pct"] = pct
            if item.get("bought") is not None:
                bought = item["bought"]
                if not isinstance(bought, bool):
                    raise ValueError(f"bought must be true or false, got {bought!r}")
                entry["bought"] = bought
            if "pct" not in entry and "bought" not in entry:
                raise ValueError("item must set at least one of pct/bought")
        except (TypeError, ValueError) as e:
            errors.append(f"couldn't understand item {i}: {e}")
            continue
        valid.append(entry)
    return valid, errors


def _validate_amount(amount, errors: list[str]):
    if amount is None:
        return None
    if isinstance(amount, bool) or not isinstance(amount, (int, float)) or pd.isna(amount) or amount < 0:
        errors.append(f"amount must be a number >= 0, got {amount!r}")
        return None
    return float(amount)


def preview_rebalance_edit(amount: float | None, items: list[dict], *, conn=None) -> dict:
    """Never writes. Returns {"before", "after", "errors"} -- "before" is the
    plan exactly as it is now; "after" applies amount/items on top of an
    IN-MEMORY copy only, never touching the database."""
    data = get_plan_with_holdings(conn=conn)
    holdings, plan = data["holdings"], data["plan"]
    before = describe_plan(holdings, plan)

    held_symbols = set(holdings["Symbol"]) if not holdings.empty else set()
    valid, errors = validate_rebalance_edits(items or [], held_symbols)
    checked_amount = _validate_amount(amount, errors)

    merged_items = {s: dict(v) for s, v in plan["items"].items()}
    for entry in valid:
        merged_items.setdefault(entry["symbol"], {"pct": 0.0, "bought": False})
        if "pct" in entry:
            merged_items[entry["symbol"]]["pct"] = entry["pct"]
        if "bought" in entry:
            merged_items[entry["symbol"]]["bought"] = entry["bought"]
    hypothetical_plan = {
        "id": plan["id"],
        "amount": checked_amount if checked_amount is not None else plan["amount"],
        "items": merged_items,
    }
    after = describe_plan(holdings, hypothetical_plan)

    return {"before": before, "after": after, "errors": errors}


def apply_rebalance_edit(amount: float | None, items: list[dict], *, conn=None) -> dict:
    """Re-fetches fresh plan/holdings state -- never trusts anything carried
    from an earlier call, same stateless rationale as trade_entry.record_trade_batch
    -- re-validates, then writes. If the edit ticks the last remaining row
    Bought, update_rebalance_plan_item() auto-completes the plan; a fresh
    blank one is started immediately after, mirroring the web page's own
    "All rows bought -- plan complete! Started a fresh one" path."""
    data = get_plan_with_holdings(conn=conn)
    holdings, plan = data["holdings"], data["plan"]

    held_symbols = set(holdings["Symbol"]) if not holdings.empty else set()
    valid, errors = validate_rebalance_edits(items or [], held_symbols)
    checked_amount = _validate_amount(amount, errors)

    applied = []
    if checked_amount is not None and checked_amount != plan["amount"]:
        db.update_rebalance_plan_amount(plan["id"], checked_amount, conn=conn)
        applied.append("amount")
    for entry in valid:
        db.update_rebalance_plan_item(plan["id"], entry["symbol"], pct=entry.get("pct"), bought=entry.get("bought"), conn=conn)
        applied.append(entry["symbol"])

    plan_completed = False
    fresh_plan = db.get_active_rebalance_plan(conn=conn)
    if fresh_plan is None:
        plan_completed = True
        db.start_rebalance_plan(holdings["Symbol"].tolist() if not holdings.empty else [], conn=conn)
        fresh_plan = db.get_active_rebalance_plan(conn=conn)

    return {
        "applied": applied,
        "errors": errors,
        "plan_completed": plan_completed,
        "after": describe_plan(holdings, fresh_plan),
    }


def preview_rebalance_reset(*, conn=None) -> dict:
    """Never writes. Describes the plan that a reset would abandon."""
    data = get_plan_with_holdings(conn=conn)
    return {"before": describe_plan(data["holdings"], data["plan"])}


def apply_rebalance_reset(*, conn=None) -> dict:
    """Re-fetches fresh state, abandons it (db.reset_rebalance_plan), and
    starts a fresh blank plan -- the same two calls the web page's own
    Reset plan button makes."""
    data = get_plan_with_holdings(conn=conn)
    holdings, plan = data["holdings"], data["plan"]
    db.reset_rebalance_plan(plan["id"], conn=conn)
    db.start_rebalance_plan(holdings["Symbol"].tolist() if not holdings.empty else [], conn=conn)
    fresh_plan = db.get_active_rebalance_plan(conn=conn)
    return {"after": describe_plan(holdings, fresh_plan)}
