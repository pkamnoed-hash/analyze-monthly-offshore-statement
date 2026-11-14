"""V4.22 -- validates, FIFO-matches, and computes win-ratio/realized P/L for option
trades, for Option Monitor's own page and (eventually) a Rich MCP tool.

Pure functions over DataFrames/dicts, no Streamlit import, DB access only through an
injectable `conn=` kwarg where relevant (callers pass an already-fetched DataFrame
here, this module itself never touches the database).

Why this is its OWN FIFO matcher, not a reuse of core/calculations.py's stock-side
`_run_fifo`: that function assumes a symbol's first trade is always a BUY that
establishes cost basis before a SELL realizes against it -- true for every stock
trade this app has ever modeled, but wrong for a short options strategy (selling to
open a covered call or cash-secured put, collecting premium up front, before buying
to close). Run through the stock pipeline, a sell-to-open would book its entire
proceeds as instant 100% profit (the FIFO lot book is empty, so nothing is
subtracted), the position would vanish from compute_current_positions while open
(quantity nets to 0), the later buy-to-close would open a phantom long lot instead of
closing anything, and apply_sequential_position_check would flag the opening sell as
a false-positive oversell. The fix here is `position_effect` ('open'/'close'): every
row says outright whether it opens or closes a position, so matching never has to
infer direction from `side` -- a short round trip (sell-to-open, buy-to-close) nets
out exactly as correctly as a long one (buy-to-open, sell-to-close), since `amount`
is always the real signed cash flow (buy negative, sell positive) regardless of which
leg came first.
"""

from collections import deque

import pandas as pd

from core.options import build_option_symbol


def validate_option_rows(rows: list[dict]) -> tuple[list[dict], list[str]]:
    """Per-row validation, mirroring core/trade_entry.py::validate_trade_rows's
    shape. A malformed row is reported back, never silently dropped or coerced."""
    valid, errors = [], []
    for i, row in enumerate(rows):
        try:
            underlying = str(row.get("underlying") or "").strip().upper()
            if not underlying:
                raise ValueError(f"underlying {row.get('underlying')!r} doesn't look like a valid ticker")

            option_type_raw = row.get("option_type")
            option_type = option_type_raw.strip().title() if isinstance(option_type_raw, str) else None
            if option_type not in ("Call", "Put"):
                raise ValueError(f"option_type must be 'Call' or 'Put', got {option_type_raw!r}")

            strike = float(row.get("strike"))
            if pd.isna(strike) or strike <= 0:
                raise ValueError(f"strike must be greater than 0, got {row.get('strike')!r}")

            expiration_date = pd.to_datetime(row.get("expiration_date"), errors="raise")
            if pd.isna(expiration_date):
                raise ValueError(f"expiration_date {row.get('expiration_date')!r} could not be parsed")

            side_raw = row.get("side")
            side = side_raw.strip().lower() if isinstance(side_raw, str) else None
            if side not in ("buy", "sell"):
                raise ValueError(f"side must be 'buy' or 'sell', got {side_raw!r}")

            effect_raw = row.get("position_effect")
            position_effect = effect_raw.strip().lower() if isinstance(effect_raw, str) else None
            if position_effect not in ("open", "close"):
                raise ValueError(f"position_effect must be 'open' or 'close', got {effect_raw!r}")

            contracts = float(row.get("contracts"))
            if pd.isna(contracts) or contracts <= 0:
                raise ValueError(f"contracts must be greater than 0, got {row.get('contracts')!r}")

            price = float(row.get("price"))
            if pd.isna(price) or price <= 0:
                raise ValueError(f"price must be greater than 0, got {row.get('price')!r}")

            trade_date = pd.to_datetime(row.get("trade_date"), errors="raise")
            if pd.isna(trade_date):
                raise ValueError(f"trade_date {row.get('trade_date')!r} could not be parsed")
        except (TypeError, ValueError) as e:
            errors.append(f"couldn't understand row {i}: {e}")
            continue

        valid.append({
            "trade_date": trade_date,
            "symbol": build_option_symbol(underlying, expiration_date, option_type, strike),
            "underlying": underlying, "option_type": option_type, "strike": strike,
            "expiration_date": expiration_date, "side": side, "position_effect": position_effect,
            "contracts": contracts, "price": price,
            "commission": row.get("commission") or 0.0, "vat": row.get("vat") or 0.0,
            "occ_fee": row.get("occ_fee") or 0.0, "orf_fee": row.get("orf_fee") or 0.0,
            "taf_fee": row.get("taf_fee") or 0.0, "sec_fee": row.get("sec_fee") or 0.0,
        })
    return valid, errors


def match_round_trips(option_trades: pd.DataFrame, *, as_of=None) -> dict:
    """FIFO-matches `open` rows against `close` rows, per `symbol` (one specific
    contract), via a lot queue keyed on `position_effect` -- see this module's own
    docstring for why that's the fix over the stock-side FIFO. Handles PARTIAL
    fills: an `open` row's `contracts` becomes one lot; a `close` row consumes
    quantity from the oldest open lot(s) first, splitting a lot's remaining
    quantity/cost proportionally if the close doesn't consume it exactly -- so
    opening 10 contracts and closing 4 now, then the other 6 later, produces two
    separate matched round trips, each with its own correct matched quantity and
    $/% P/L, not one.

    `pl` for a matched pair is the sum of both legs' signed `amount` for the
    matched quantity -- correct regardless of which leg was buy or sell. `pl_pct`
    is `pl / abs(the opening leg's matched cash flow) * 100`, computed the same way
    for a long or a short (relative to the premium that actually changed hands at
    open, not a margin/collateral figure this app doesn't track).

    Any remaining unmatched `open` quantity stays in `"open"`. One whose
    `expiration_date` is on or before `as_of` (default: today) auto-resolves into
    `"expired"` -- worthless for a long (full remaining premium paid is the loss),
    kept in full for a short (full remaining premium collected is the gain), since
    `pl` there is just that lot's own remaining signed cash flow. This is the
    stated v1 simplification that American-style exercise/assignment isn't
    modeled -- see docs/ROADMAP.md's V4.22 section for why that's an accepted gap,
    not a silent miscalculation.

    Returns `{"closed": [...], "open": [...], "expired": [...], "errors": [...]}` --
    `errors` names a symbol/date where more was closed than was ever recorded open
    (a data-entry mistake), never silently absorbed into the matching."""
    if option_trades.empty:
        return {"closed": [], "open": [], "expired": [], "errors": []}

    now = pd.Timestamp(as_of) if as_of is not None else pd.Timestamp.now().normalize()

    tx = option_trades.copy()
    tx["_effect_order"] = (tx["position_effect"] == "close").astype(int)  # open (0) before close (1), same day
    tx = tx.sort_values(["symbol", "trade_date", "_effect_order"])

    books: dict[str, deque] = {}
    meta: dict[str, dict] = {}
    closed, errors = [], []

    for _, row in tx.iterrows():
        sym = row["symbol"]
        book = books.setdefault(sym, deque())
        meta.setdefault(sym, {
            "underlying": row["underlying"], "option_type": row["option_type"],
            "strike": row["strike"], "expiration_date": row["expiration_date"],
        })
        contracts = row["contracts"]
        amount = row["amount"]

        if row["position_effect"] == "open":
            per_contract = (amount / contracts) if contracts else 0.0
            # Last two elements are the lot's ORIGINAL size and its source ('manual'/
            # 'slip'/'migrated-seed') -- kept fixed even as the first (remaining)
            # element is consumed below. Original size is what "X of Y contracts" (Y)
            # means for THIS lot specifically, not a sum across every lot for the
            # symbol (a second, separate open for the same contract gets its own
            # independent "X of Y", not merged into the first). Source is what lets a
            # caller (Option Monitor) show Delete only for a manual/slip row, never a
            # migrated-seed one -- same restriction delete_trade/delete_dividend's
            # own pages already enforce.
            book.append([
                contracts, per_contract, row["trade_date"], row["side"], row.get("id"),
                contracts, row.get("source"),
            ])
            continue

        # position_effect == "close"
        remaining = contracts
        while remaining > 1e-9 and book:
            lot_contracts, per_contract, opened_date, open_side, open_id, original_contracts, open_source = book[0]
            take = min(lot_contracts, remaining)
            open_amount_share = take * per_contract
            close_amount_share = (take / contracts) * amount
            pl = open_amount_share + close_amount_share
            pl_pct = (pl / abs(open_amount_share) * 100) if open_amount_share else 0.0
            closed.append({
                "symbol": sym, **meta[sym],
                "contracts": take, "side_open": open_side, "side_close": row["side"],
                "opened": opened_date, "closed": row["trade_date"],
                "open_amount": open_amount_share, "close_amount": close_amount_share,
                "pl": pl, "pl_pct": pl_pct, "open_id": open_id, "close_id": row.get("id"),
                "open_source": open_source, "close_source": row.get("source"),
            })
            lot_contracts -= take
            remaining -= take
            if lot_contracts <= 1e-9:
                book.popleft()
            else:
                book[0][0] = lot_contracts
        if remaining > 1e-9:
            errors.append(
                f"{sym}: closed {remaining:g} more contract(s) on "
                f"{pd.Timestamp(row['trade_date']).date()} than were ever recorded open"
            )

    open_rows, expired = [], []
    for sym, book in books.items():
        for lot_contracts, per_contract, opened_date, open_side, open_id, original_contracts, open_source in book:
            if lot_contracts <= 1e-9:
                continue
            open_amount_share = lot_contracts * per_contract
            entry = {
                "symbol": sym, **meta[sym],
                "contracts": lot_contracts, "total_contracts": original_contracts,
                "side_open": open_side, "opened": opened_date,
                "open_amount": open_amount_share, "open_id": open_id, "open_source": open_source,
            }
            if pd.Timestamp(meta[sym]["expiration_date"]) <= now:
                pl = open_amount_share  # remaining premium: full loss (long) / full gain (short)
                entry["pl"] = pl
                entry["pl_pct"] = (pl / abs(open_amount_share) * 100) if open_amount_share else 0.0
                expired.append(entry)
            else:
                open_rows.append(entry)

    return {"closed": closed, "open": open_rows, "expired": expired, "errors": errors}


def resolved_events(option_trades: pd.DataFrame, *, as_of=None) -> pd.DataFrame:
    """Every resolved (closed + expired) option round trip, shaped like
    core/calculations.py's own realized-events DataFrame (Trade Date, Realized
    P/L) -- so compute_investment_gain can fold option P/L into Dashboard's
    "Investment Gain/Loss" the same way it already treats stock P/L and
    dividends: filtered to a date range, summed. The resolution date is the
    CLOSE date for a closed round trip, or the EXPIRATION date for one that
    auto-resolved as expired -- that's when it was actually realized, not the
    date it was originally opened."""
    result = match_round_trips(option_trades, as_of=as_of)
    rows = (
        [{"Trade Date": pd.Timestamp(r["closed"]), "Realized P/L": r["pl"]} for r in result["closed"]]
        + [{"Trade Date": pd.Timestamp(r["expiration_date"]), "Realized P/L": r["pl"]} for r in result["expired"]]
    )
    return pd.DataFrame(rows, columns=["Trade Date", "Realized P/L"])


def compute_option_summary(option_trades: pd.DataFrame, *, as_of=None) -> dict:
    """win_count/loss_count/win_rate_pct/total_realized_pl over every RESOLVED
    round trip (closed + expired), plus the still-open (not yet expired)
    positions for display. A tie (pl == 0) counts as a loss, not a win -- matches
    this app's "Win" badge only ever meaning pl > 0.

    `total_pl_pct` is the portfolio-wide version of each trip's own `pl_pct`:
    total realized P/L divided by the total premium that changed hands opening
    every resolved trip (not a simple average of each trip's `pl_pct`, which
    would weight a 1-contract trip the same as a 100-contract one)."""
    result = match_round_trips(option_trades, as_of=as_of)
    resolved = result["closed"] + result["expired"]
    win_count = sum(1 for r in resolved if r["pl"] > 0)
    loss_count = len(resolved) - win_count
    total_realized_pl = sum(r["pl"] for r in resolved)
    win_rate_pct = (win_count / len(resolved) * 100) if resolved else 0.0
    total_opening_premium = sum(abs(r["open_amount"]) for r in resolved)
    total_pl_pct = (total_realized_pl / total_opening_premium * 100) if total_opening_premium else 0.0
    return {
        "win_count": win_count, "loss_count": loss_count, "win_rate_pct": win_rate_pct,
        "total_realized_pl": total_realized_pl, "total_pl_pct": total_pl_pct,
        "closed": result["closed"], "expired": result["expired"], "open": result["open"],
        "errors": result["errors"],
    }
