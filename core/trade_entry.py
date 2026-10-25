"""V4.19 -- validates, matches against what's already in the `trades` table, and
writes a BATCH of real buy/sell trades, for Rich's record_trades_batch MCP tool
(the only other caller of core/db.py's insert_trade besides app_pages/record_trade.py).

Pure functions over DataFrames/dicts, no Streamlit import, DB access only through an
injectable `conn=` kwarg (same shape as core/fundamentals_capture.py). Validation rules
mirror Record Trade's own (app_pages/record_trade.py) -- this is also where a real gap
in db.py::insert_trade gets closed: that function does `side.lower()` with no check that
the result is actually "buy"/"sell", so a typo there would silently fall into the sell
branch. validate_trade_rows is the first place that's ever actually checked.
"""

import pandas as pd

from core import calculations, db
from core.fundamentals_summary import normalize_symbol

_CANDIDATE_COLUMNS = [
    "trade_date", "side", "symbol", "quantity", "price",
    "commission_fee", "vat", "reserved_fee", "fee_rebate", "order_type", "order_id",
]


def validate_trade_rows(rows: list[dict]) -> tuple[list[dict], list[str]]:
    """Per-row validation. A malformed row is reported back, never silently dropped
    or silently coerced (e.g. a bad `side` must error, not fall through to 'sell')."""
    valid, errors = [], []
    for i, row in enumerate(rows):
        try:
            symbol = normalize_symbol(row.get("symbol"))
            if symbol is None:
                raise ValueError(f"symbol {row.get('symbol')!r} doesn't look like a valid ticker")

            side_raw = row.get("side")
            side = side_raw.strip().lower() if isinstance(side_raw, str) else None
            if side not in ("buy", "sell"):
                raise ValueError(f"side must be 'buy' or 'sell', got {side_raw!r}")

            quantity = float(row.get("quantity"))
            if pd.isna(quantity) or quantity <= 0:
                raise ValueError(f"quantity must be greater than 0, got {row.get('quantity')!r}")

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
            "trade_date": trade_date, "side": side, "symbol": symbol,
            "quantity": quantity, "price": price,
            "commission_fee": row.get("commission_fee") or 0.0,
            "vat": row.get("vat") or 0.0,
            "reserved_fee": row.get("reserved_fee") or 0.0,
            "fee_rebate": row.get("fee_rebate") or 0.0,
            "order_type": row.get("order_type"),
            "order_id": row.get("order_id"),
        })
    return valid, errors


def match_against_existing_trades(candidates: pd.DataFrame, db_trades: pd.DataFrame) -> pd.DataFrame:
    """Adds `duplicate_in_batch` (an earlier row in THIS batch already has the same
    key -- covers overlapping screenshots, or the same row appearing twice in one
    scrollable list) and `matched` (a trade with this exact key already exists in
    the live `trades` table). Key: (Trade Date, Symbol, round(signed Quantity, 6),
    round(Price, 4)) -- same as reconciliation.match_trades(), just matched against
    the live table instead of the official xlsx."""
    df = candidates.reset_index(drop=True).copy()
    df["_signed_qty"] = df.apply(lambda r: r["quantity"] if r["side"] == "buy" else -r["quantity"], axis=1)
    df["_qty_key"] = df["_signed_qty"].round(6)
    df["_price_key"] = df["price"].round(4)

    seen = set()
    dup_flags = []
    for key in zip(df["trade_date"], df["symbol"], df["_qty_key"], df["_price_key"]):
        dup_flags.append(key in seen)
        seen.add(key)
    df["duplicate_in_batch"] = dup_flags

    df["matched"] = False
    if not db_trades.empty:
        from core import reconciliation

        left = df[~df["duplicate_in_batch"]]
        right = db_trades.copy()
        right["_qty_key"] = right["Quantity"].round(6)
        right["_price_key"] = right["Price"].round(4)
        right = right.rename(columns={"Trade Date": "trade_date", "Symbol": "symbol"})
        right = right[["trade_date", "symbol", "_qty_key", "_price_key"]]

        key_cols = ["trade_date", "symbol", "_qty_key", "_price_key"]
        paired = reconciliation.pair_1to1(left, right, key_cols)
        df.loc[~df["duplicate_in_batch"], "matched"] = (paired["_merge"] == "both").values

    return df.drop(columns=["_signed_qty"])


def apply_sequential_position_check(new_candidates: pd.DataFrame, db_trades: pd.DataFrame) -> pd.DataFrame:
    """Flags `is_oversell` for a sell that exceeds the position AT THAT POINT, walking
    candidates in trade-date order against a running position -- multiple trades in one
    batch affect each other (a buy earlier in the same batch can cover a later sell).
    Reuses calculations.compute_current_positions (so stock splits/ReOrg CA already in
    the DB are accounted for correctly) recomputed incrementally; batches are small
    (a screenshot catch-up), so the O(n^2) re-computation is not a real cost. Never
    blocks -- same permissive warn-not-block policy as Record Trade's own oversell
    checkbox; the caller decides what to do with the flag."""
    result = new_candidates.sort_values("trade_date").reset_index(drop=True).copy()
    result["is_oversell"] = False
    if result.empty:
        return result

    accepted_rows = []
    for i, cand in result.iterrows():
        as_of = db_trades[db_trades["Trade Date"] <= cand["trade_date"]]
        if accepted_rows:
            as_of = pd.concat([as_of, pd.DataFrame(accepted_rows)], ignore_index=True)
        positions = calculations.compute_current_positions(as_of)
        match = positions[positions["Symbol"] == cand["symbol"]]
        held = match.iloc[0]["Quantity"] if not match.empty else 0.0

        if cand["side"] == "sell" and cand["quantity"] > held + 1e-9:
            result.at[i, "is_oversell"] = True

        signed_qty = cand["quantity"] if cand["side"] == "buy" else -cand["quantity"]
        accepted_rows.append({
            "Symbol": cand["symbol"], "Trade Date": cand["trade_date"], "Entry Type": "Trade Entry",
            "Side": cand["side"], "Quantity": signed_qty, "Price": cand["price"],
            "Amount": 0.0, "Commission": 0.0,
            "Month": pd.Timestamp(cand["trade_date"]).to_period("M").to_timestamp(),
        })
    return result


def _trade_records(df: pd.DataFrame, extra_cols: list[str] = ()) -> list[dict]:
    if df.empty:
        return []
    cols = _CANDIDATE_COLUMNS + list(extra_cols)
    cols = [c for c in cols if c in df.columns]
    return df[cols].to_dict("records")


def preview_trade_batch(rows: list[dict], *, conn=None) -> dict:
    """Never writes. Returns {"new", "already_recorded", "oversell_rows", "errors"} --
    each of the first three is a list of plain dicts shaped like `rows`' own input,
    `already_recorded` items also carry a "reason" (either "already recorded" or
    "duplicate within this batch")."""
    valid, errors = validate_trade_rows(rows)
    if not valid:
        return {"new": [], "already_recorded": [], "oversell_rows": [], "errors": errors}

    candidates = pd.DataFrame(valid)
    db_trades = db.fetch_trades(conn=conn)
    matched = match_against_existing_trades(candidates, db_trades)

    already_mask = matched["matched"] | matched["duplicate_in_batch"]
    already = matched[already_mask].copy()
    already["reason"] = already["duplicate_in_batch"].map(
        {True: "duplicate within this batch", False: "already recorded"}
    )
    new = matched[~already_mask].copy()
    new = apply_sequential_position_check(new, db_trades)

    oversell = new[new["is_oversell"]] if not new.empty else new

    return {
        "new": _trade_records(new, extra_cols=["is_oversell"]),
        "already_recorded": _trade_records(already, extra_cols=["reason"]),
        "oversell_rows": _trade_records(oversell),
        "errors": errors,
    }


def _fmt_trade(r: dict) -> str:
    date = pd.Timestamp(r["trade_date"]).strftime("%Y-%m-%d")
    return f"{date} {r['side'].title()} {r['quantity']:g} {r['symbol']} @ {r['price']:g}"


def describe_trade_batch_preview(result: dict) -> str:
    """The chat text shown to the user before any write -- lists exactly what's being
    skipped and why, and exactly what's new, so the matching logic's correctness is
    visible before every confirm, not hidden behind a bare row count."""
    lines = []
    total = len(result["new"]) + len(result["already_recorded"]) + len(result["errors"])
    lines.append(f"Found {total} row(s).")

    if result["errors"]:
        lines.append(f"{len(result['errors'])} couldn't be understood:")
        for e in result["errors"]:
            lines.append(f"  - {e}")

    if result["already_recorded"]:
        lines.append(f"{len(result['already_recorded'])} already recorded (skipping):")
        for r in result["already_recorded"]:
            lines.append(f"  - {_fmt_trade(r)} ({r['reason']})")

    if result["new"]:
        lines.append(f"{len(result['new'])} new, would record:")
        for r in result["new"]:
            tag = " [OVERSELL WARNING -- exceeds position at that date]" if r.get("is_oversell") else ""
            lines.append(f"  - {_fmt_trade(r)}{tag}")
        lines.append(f"Reply to confirm recording all {len(result['new'])} new row(s).")
    else:
        lines.append("Nothing new to record.")

    return "\n".join(lines)


def record_trade_batch(rows: list[dict], *, conn=None) -> dict:
    """Re-runs the full preview against CURRENT state -- never trusts anything carried
    from an earlier call -- then writes every row still "new". Best-effort per row, not
    one atomic transaction (a known v1 limitation, matching entering several rows by
    hand one at a time -- not worth the added complexity here yet)."""
    preview = preview_trade_batch(rows, conn=conn)
    recorded, failed = [], []
    for item in preview["new"]:
        try:
            db.insert_trade(
                trade_date=pd.Timestamp(item["trade_date"]).strftime("%Y-%m-%d"),
                side=item["side"], symbol=item["symbol"],
                quantity=item["quantity"], price=item["price"],
                commission_fee=item.get("commission_fee") or 0.0, vat=item.get("vat") or 0.0,
                reserved_fee=item.get("reserved_fee") or 0.0, fee_rebate=item.get("fee_rebate") or 0.0,
                order_type=item.get("order_type"), order_id=item.get("order_id"),
                source="manual", conn=conn,
            )
            recorded.append(item)
        except Exception as e:
            failed.append({**item, "error": str(e)})

    return {
        "recorded": recorded,
        "skipped_already_recorded": preview["already_recorded"],
        "failed": failed,
    }
