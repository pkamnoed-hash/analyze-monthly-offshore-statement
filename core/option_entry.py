"""V4.23 -- validates, matches against what's already in `option_trades`, and
writes a BATCH of option trades, for Rich's record_option_trades_batch MCP tool.
Mirrors core/trade_entry.py's shape exactly (that module's own docstring explains
the pattern); the one real difference is WHAT counts as an unsafe row: there's no
"short stock" ambiguity to check for here (position_effect already says outright
whether a row opens or closes a position -- see core/option_pl.py's own docstring),
but closing more contracts than are currently open for a symbol is still a real
data-entry mistake worth flagging before it's saved, so that's this module's
equivalent of trade_entry.py's oversell check.

Pure functions over DataFrames/dicts, no Streamlit import, DB access only through
an injectable `conn=` kwarg (same shape as core/trade_entry.py)."""

import pandas as pd

from core import db, option_pl

_CANDIDATE_COLUMNS = [
    "trade_date", "symbol", "underlying", "option_type", "strike", "expiration_date",
    "side", "position_effect", "contracts", "price",
    "commission", "vat", "occ_fee", "orf_fee", "taf_fee", "sec_fee",
]


def _compute_amount(row: dict) -> float:
    """Same formula as db.py::insert_option_trade -- duplicated rather than shared
    since insert_option_trade computes it as a side effect of writing, and this
    module needs it earlier, before any write, to feed option_pl.match_round_trips
    during the overclose check below."""
    gross = row["contracts"] * row["price"] * 100
    total_fees = sum(
        row.get(f) or 0.0 for f in ("commission", "vat", "occ_fee", "orf_fee", "taf_fee", "sec_fee")
    )
    return -(gross + total_fees) if row["side"] == "buy" else (gross - total_fees)


def match_against_existing_option_trades(candidates: pd.DataFrame, db_options: pd.DataFrame) -> pd.DataFrame:
    """Adds `duplicate_in_batch` (an earlier row in THIS batch already has the same
    key) and `matched` (a trade with this exact key already exists in the live
    `option_trades` table). Key: (trade_date, symbol, side, position_effect,
    round(contracts, 6), round(price, 4)) -- position_effect takes the place
    trade_entry.py's signed quantity plays for stocks, since it's what tells an
    open and a close for the same symbol/date/price apart."""
    df = candidates.reset_index(drop=True).copy()
    df["_qty_key"] = df["contracts"].round(6)
    df["_price_key"] = df["price"].round(4)

    seen = set()
    dup_flags = []
    for key in zip(
        df["trade_date"], df["symbol"], df["side"], df["position_effect"], df["_qty_key"], df["_price_key"],
    ):
        dup_flags.append(key in seen)
        seen.add(key)
    df["duplicate_in_batch"] = dup_flags

    df["matched"] = False
    if not db_options.empty:
        from core import reconciliation

        left = df[~df["duplicate_in_batch"]]
        right = db_options.copy()
        right["_qty_key"] = right["contracts"].round(6)
        right["_price_key"] = right["price"].round(4)
        right = right[["trade_date", "symbol", "side", "position_effect", "_qty_key", "_price_key"]]

        key_cols = ["trade_date", "symbol", "side", "position_effect", "_qty_key", "_price_key"]
        paired = reconciliation.pair_1to1(left, right, key_cols)
        df.loc[~df["duplicate_in_batch"], "matched"] = (paired["_merge"] == "both").values

    return df


def apply_overclose_check(new_candidates: pd.DataFrame, db_options: pd.DataFrame) -> pd.DataFrame:
    """Flags `is_overclose` for a close that would exceed what's currently open for
    that symbol AT THAT POINT, walking candidates in trade-date order against a
    running book built from existing DB rows plus whatever's been accepted so far
    in THIS batch (so an open recorded earlier in the same batch can still cover a
    close later in it) -- same sequential-walk shape as trade_entry.py's
    apply_sequential_position_check, just reusing option_pl.match_round_trips's own
    built-in overclose detection (its `errors` list) instead of a hand-rolled
    position tally. Never blocks -- the caller decides what to do with the flag."""
    result = new_candidates.sort_values("trade_date").reset_index(drop=True).copy()
    result["is_overclose"] = False
    if result.empty:
        return result

    accepted_rows: list[dict] = []
    for i, cand in result.iterrows():
        sym = cand["symbol"]
        book_so_far = db_options[db_options["symbol"] == sym] if not db_options.empty else db_options
        same_symbol_accepted = [r for r in accepted_rows if r["symbol"] == sym]
        if same_symbol_accepted:
            book_so_far = pd.concat([book_so_far, pd.DataFrame(same_symbol_accepted)], ignore_index=True)

        if cand["position_effect"] == "close":
            combined = pd.concat([book_so_far, pd.DataFrame([cand.to_dict()])], ignore_index=True)
            check = option_pl.match_round_trips(combined)
            if check["errors"]:
                result.at[i, "is_overclose"] = True
        accepted_rows.append(cand.to_dict())
    return result


def _option_records(df: pd.DataFrame, extra_cols: list[str] = ()) -> list[dict]:
    if df.empty:
        return []
    cols = _CANDIDATE_COLUMNS + list(extra_cols)
    cols = [c for c in cols if c in df.columns]
    return df[cols].to_dict("records")


def preview_option_batch(rows: list[dict], *, conn=None) -> dict:
    """Never writes. Returns {"new", "already_recorded", "overclose_rows", "errors"}
    -- each of the first three is a list of plain dicts shaped like `rows`' own
    input, `already_recorded` items also carry a "reason" (either "already
    recorded" or "duplicate within this batch")."""
    valid, errors = option_pl.validate_option_rows(rows)
    if not valid:
        return {"new": [], "already_recorded": [], "overclose_rows": [], "errors": errors}

    candidates = pd.DataFrame(valid)
    candidates["amount"] = candidates.apply(lambda r: _compute_amount(r), axis=1)

    db_options = db.fetch_option_trades(conn=conn)
    matched = match_against_existing_option_trades(candidates, db_options)

    already_mask = matched["matched"] | matched["duplicate_in_batch"]
    already = matched[already_mask].copy()
    already["reason"] = already["duplicate_in_batch"].map(
        {True: "duplicate within this batch", False: "already recorded"}
    )
    new = matched[~already_mask].copy()
    new = apply_overclose_check(new, db_options)

    overclose = new[new["is_overclose"]] if not new.empty else new

    return {
        "new": _option_records(new, extra_cols=["is_overclose"]),
        "already_recorded": _option_records(already, extra_cols=["reason"]),
        "overclose_rows": _option_records(overclose),
        "errors": errors,
    }


def _fmt_option(r: dict) -> str:
    date = pd.Timestamp(r["trade_date"]).strftime("%Y-%m-%d")
    return f"{date} {r['side'].title()} to {r['position_effect']} {r['contracts']:g} {r['symbol']} @ {r['price']:g}"


def describe_option_batch_preview(result: dict) -> str:
    """The chat text shown to the user before any write -- lists exactly what's
    being skipped and why, and exactly what's new, so the matching logic's
    correctness is visible before every confirm, not hidden behind a bare row
    count. Mirrors trade_entry.py's describe_trade_batch_preview."""
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
            lines.append(f"  - {_fmt_option(r)} ({r['reason']})")

    if result["new"]:
        lines.append(f"{len(result['new'])} new, would record:")
        for r in result["new"]:
            tag = " [OVERCLOSE WARNING -- exceeds open contracts for this symbol at that date]" if r.get("is_overclose") else ""
            lines.append(f"  - {_fmt_option(r)}{tag}")
        lines.append(f"Reply to confirm recording all {len(result['new'])} new row(s).")
    else:
        lines.append("Nothing new to record.")

    return "\n".join(lines)


def record_option_batch(rows: list[dict], *, conn=None) -> dict:
    """Re-runs the full preview against CURRENT state -- never trusts anything
    carried from an earlier call -- then writes every row still "new". Best-effort
    per row, not one atomic transaction, matching trade_entry.py's own
    record_trade_batch."""
    preview = preview_option_batch(rows, conn=conn)
    recorded, failed = [], []
    for item in preview["new"]:
        try:
            db.insert_option_trade(
                trade_date=pd.Timestamp(item["trade_date"]).strftime("%Y-%m-%d"),
                symbol=item["symbol"], underlying=item["underlying"],
                option_type=item["option_type"], strike=item["strike"],
                expiration_date=pd.Timestamp(item["expiration_date"]).strftime("%Y-%m-%d"),
                side=item["side"], position_effect=item["position_effect"],
                contracts=item["contracts"], price=item["price"],
                commission=item.get("commission") or 0.0, vat=item.get("vat") or 0.0,
                occ_fee=item.get("occ_fee") or 0.0, orf_fee=item.get("orf_fee") or 0.0,
                taf_fee=item.get("taf_fee") or 0.0, sec_fee=item.get("sec_fee") or 0.0,
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
