"""V4.19 -- validates, matches against what's already in the `dividends` table, and
writes a BATCH of real dividends/interest/capital distributions, for Rich's
record_dividends_batch MCP tool. Same shape as core/trade_entry.py (that module's
docstring explains the shared design); no oversell concept here, no sequential
position logic needed.
"""

import pandas as pd

from core import db
from core.fundamentals_summary import normalize_symbol

ENTRY_TYPES = ["Dividend", "Interest", "Capital Distribution"]  # mirrors app_pages/record_dividend.py exactly

_CANDIDATE_COLUMNS = ["trade_date", "entry_type", "symbol", "gross_amount", "withholding_tax", "net_amount"]


def validate_dividend_rows(rows: list[dict]) -> tuple[list[dict], list[str]]:
    """Per-row validation mirroring Record Dividend's own rules: entry_type must be a
    known value; symbol is required unless entry_type == "Interest" (same rule, same
    wording as the page); net = round(gross - withholding, 2), never float noise."""
    valid, errors = [], []
    for i, row in enumerate(rows):
        try:
            entry_type = row.get("entry_type")
            if entry_type not in ENTRY_TYPES:
                raise ValueError(f"entry_type must be one of {ENTRY_TYPES}, got {entry_type!r}")

            gross_amount = float(row.get("gross_amount"))
            if pd.isna(gross_amount) or gross_amount <= 0:
                raise ValueError(f"gross_amount must be greater than 0, got {row.get('gross_amount')!r}")

            withholding_tax = float(row.get("withholding_tax") or 0.0)
            if pd.isna(withholding_tax) or withholding_tax < 0:
                raise ValueError(f"withholding_tax cannot be negative, got {row.get('withholding_tax')!r}")

            symbol = normalize_symbol(row.get("symbol")) if row.get("symbol") else None
            if entry_type != "Interest" and not symbol:
                raise ValueError("symbol is required unless entry_type is 'Interest'")

            trade_date = pd.to_datetime(row.get("trade_date"), errors="raise")
            if pd.isna(trade_date):
                raise ValueError(f"trade_date {row.get('trade_date')!r} could not be parsed")
        except (TypeError, ValueError) as e:
            errors.append(f"couldn't understand row {i}: {e}")
            continue

        valid.append({
            "trade_date": trade_date, "entry_type": entry_type, "symbol": symbol,
            "gross_amount": gross_amount, "withholding_tax": withholding_tax,
            "net_amount": round(gross_amount - withholding_tax, 2),
        })
    return valid, errors


def match_against_existing_dividends(candidates: pd.DataFrame, db_dividends: pd.DataFrame) -> pd.DataFrame:
    """Same shape as trade_entry.match_against_existing_trades: `duplicate_in_batch`
    for a repeat within this batch, `matched` for an exact counterpart already in the
    live `dividends` table. Key: (Trade Date, Symbol, round(Net Amt, 2))."""
    df = candidates.reset_index(drop=True).copy()
    df["_amt_key"] = df["net_amount"].round(2)

    seen = set()
    dup_flags = []
    for key in zip(df["trade_date"], df["symbol"], df["_amt_key"]):
        dup_flags.append(key in seen)
        seen.add(key)
    df["duplicate_in_batch"] = dup_flags

    df["matched"] = False
    if not db_dividends.empty:
        from core import reconciliation

        left = df[~df["duplicate_in_batch"]]
        right = db_dividends.copy()
        right["_amt_key"] = right["Net Amt"].round(2)
        right = right.rename(columns={"Trade Date": "trade_date", "Symbol": "symbol"})
        right = right[["trade_date", "symbol", "_amt_key"]]

        key_cols = ["trade_date", "symbol", "_amt_key"]
        paired = reconciliation.pair_1to1(left, right, key_cols)
        df.loc[~df["duplicate_in_batch"], "matched"] = (paired["_merge"] == "both").values

    return df


def _dividend_records(df: pd.DataFrame, extra_cols: list[str] = ()) -> list[dict]:
    if df.empty:
        return []
    cols = _CANDIDATE_COLUMNS + list(extra_cols)
    cols = [c for c in cols if c in df.columns]
    return df[cols].to_dict("records")


def preview_dividend_batch(rows: list[dict], *, conn=None) -> dict:
    """Never writes. Returns {"new", "already_recorded", "errors"}."""
    valid, errors = validate_dividend_rows(rows)
    if not valid:
        return {"new": [], "already_recorded": [], "errors": errors}

    candidates = pd.DataFrame(valid)
    db_dividends = db.fetch_dividends(conn=conn)
    matched = match_against_existing_dividends(candidates, db_dividends)

    already_mask = matched["matched"] | matched["duplicate_in_batch"]
    already = matched[already_mask].copy()
    already["reason"] = already["duplicate_in_batch"].map(
        {True: "duplicate within this batch", False: "already recorded"}
    )
    new = matched[~already_mask].copy().sort_values("trade_date")

    return {
        "new": _dividend_records(new),
        "already_recorded": _dividend_records(already, extra_cols=["reason"]),
        "errors": errors,
    }


def _fmt_dividend(r: dict) -> str:
    date = pd.Timestamp(r["trade_date"]).strftime("%Y-%m-%d")
    symbol = r["symbol"] or "(no symbol)"
    return f"{date} {r['entry_type']} {symbol} net {r['net_amount']:g} (gross {r['gross_amount']:g}, withholding {r['withholding_tax']:g})"


def describe_dividend_batch_preview(result: dict) -> str:
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
            lines.append(f"  - {_fmt_dividend(r)} ({r['reason']})")

    if result["new"]:
        lines.append(f"{len(result['new'])} new, would record:")
        for r in result["new"]:
            lines.append(f"  - {_fmt_dividend(r)}")
        lines.append(f"Reply to confirm recording all {len(result['new'])} new row(s).")
    else:
        lines.append("Nothing new to record.")

    return "\n".join(lines)


def record_dividend_batch(rows: list[dict], *, conn=None) -> dict:
    """Re-runs the full preview against CURRENT state, then writes every row still
    "new". Best-effort per row, not one atomic transaction (same known v1 limitation
    as trade_entry.record_trade_batch)."""
    preview = preview_dividend_batch(rows, conn=conn)
    recorded, failed = [], []
    for item in preview["new"]:
        try:
            db.insert_dividend(
                trade_date=pd.Timestamp(item["trade_date"]).strftime("%Y-%m-%d"),
                entry_type=item["entry_type"],
                net_amount=item["net_amount"], symbol=item["symbol"],
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
