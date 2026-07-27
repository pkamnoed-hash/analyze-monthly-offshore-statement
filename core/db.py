"""SQLite persistence for user-entered trades and dividends.

Pure logic, no Streamlit import -- every function accepts an optional
injected `conn` so tests can pass an in-memory database instead of the real
file (see tests/test_db.py).
"""

import os
import sqlite3

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(PROJECT_ROOT, "data", "portfolio.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date    TEXT NOT NULL,
    entry_type    TEXT NOT NULL DEFAULT 'Trade Entry',
    side          TEXT,
    symbol        TEXT NOT NULL,
    description   TEXT,
    quantity      REAL NOT NULL,
    price         REAL,
    amount        REAL,
    commission    REAL,
    vat           REAL,
    reserved_fee  REAL,
    fee_rebate    REAL,
    order_id      TEXT,
    order_type    TEXT,
    source        TEXT NOT NULL DEFAULT 'manual',
    reconciled_month TEXT,
    notes         TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_trades_symbol_date ON trades(symbol, trade_date);

CREATE TABLE IF NOT EXISTS dividends (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date  TEXT NOT NULL,
    symbol      TEXT,
    entry_type  TEXT NOT NULL CHECK(entry_type IN ('Dividend','Interest','Capital Distribution')),
    net_amount  REAL NOT NULL,
    source      TEXT NOT NULL DEFAULT 'manual',
    reconciled_month TEXT,
    notes       TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_dividends_symbol_date ON dividends(symbol, trade_date);

CREATE TABLE IF NOT EXISTS symbol_types (
    symbol           TEXT PRIMARY KEY,
    allocation_type  TEXT NOT NULL CHECK(allocation_type IN ('Dividend', 'Growth')),
    updated_at       TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

TRADE_COLUMNS = [
    "trade_date", "entry_type", "side", "symbol", "description", "quantity", "price",
    "amount", "commission", "vat", "reserved_fee", "fee_rebate", "order_id", "order_type",
    "source", "notes",
]
DIVIDEND_COLUMNS = ["trade_date", "symbol", "entry_type", "net_amount", "source", "notes"]


def get_connection():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    return sqlite3.connect(DB_PATH)


def _with_connection(conn):
    """Returns (conn, should_close). Opens the real DB file if none was injected."""
    if conn is not None:
        return conn, False
    return get_connection(), True


def init_db(conn=None):
    c, should_close = _with_connection(conn)
    c.executescript(SCHEMA)
    c.commit()
    if should_close:
        c.close()


def compute_net_commission(commission_fee=0.0, vat=0.0, reserved_fee=0.0, fee_rebate=0.0):
    """commission = commission_fee + vat + reserved_fee (SEC+TAF on sells) - fee_rebate
    (e.g. a 'Monthly Free Trade' coupon). One formula for both buy and sell: on a buy,
    reserved_fee/fee_rebate are simply 0, so it reduces to commission_fee + vat.
    Verified against a real sell slip: 1.04 + 0.00 + 0.03 - 1.04 = 0.03, and
    689.98 (gross Stock Amount) - 0.03 = 689.95, matching the slip's printed
    Total Credit exactly."""
    return (commission_fee or 0.0) + (vat or 0.0) + (reserved_fee or 0.0) - (fee_rebate or 0.0)


def insert_trade(
    *, trade_date, side, symbol, quantity, price,
    commission_fee=0.0, vat=0.0, reserved_fee=0.0, fee_rebate=0.0,
    entry_type="Trade Entry", description=None, order_id=None, order_type=None,
    source="manual", notes=None, conn=None,
):
    """Entry point for manually-entered or slip-parsed trades (Record Trade page).
    `quantity` is a POSITIVE magnitude and `side` is 'buy'/'sell' -- sign conversion
    to the xlsx Transactions convention (+buy, -sell for both quantity and amount)
    happens here, once, along with fee-netting via compute_net_commission(). Seed
    rows from the official xlsx bypass this (already correctly signed/netted) and
    use insert_trade_raw() instead."""
    side = side.lower()
    signed_quantity = quantity if side == "buy" else -quantity
    gross_amount = quantity * price
    signed_amount = -gross_amount if side == "buy" else gross_amount
    commission = compute_net_commission(commission_fee, vat, reserved_fee, fee_rebate)

    row = {
        "trade_date": trade_date, "entry_type": entry_type, "side": side, "symbol": symbol,
        "description": description, "quantity": signed_quantity, "price": price,
        "amount": signed_amount, "commission": commission, "vat": vat,
        "reserved_fee": reserved_fee, "fee_rebate": fee_rebate, "order_id": order_id,
        "order_type": order_type, "source": source, "notes": notes,
    }
    insert_trade_raw(row, conn=conn)


def insert_trade_raw(row: dict, conn=None):
    """Inserts a trades row as-is -- quantity/amount already signed, commission
    already netted. Used internally by insert_trade() and by the seed script,
    which reads already-correct values straight from the audited xlsx."""
    c, should_close = _with_connection(conn)
    values = [row.get(col) for col in TRADE_COLUMNS]
    placeholders = ",".join("?" for _ in TRADE_COLUMNS)
    c.execute(f"INSERT INTO trades ({','.join(TRADE_COLUMNS)}) VALUES ({placeholders})", values)
    c.commit()
    if should_close:
        c.close()


def insert_trades_bulk(rows: list, conn=None):
    """Raw bulk insert (each row already fully-formed, see insert_trade_raw), one
    transaction -- used by the seed script. Record Trade page always inserts one
    trade at a time via insert_trade(), so this isn't the UI's code path."""
    c, should_close = _with_connection(conn)
    try:
        placeholders = ",".join("?" for _ in TRADE_COLUMNS)
        c.executemany(
            f"INSERT INTO trades ({','.join(TRADE_COLUMNS)}) VALUES ({placeholders})",
            [[row.get(col) for col in TRADE_COLUMNS] for row in rows],
        )
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        if should_close:
            c.close()


def insert_dividend(
    *, trade_date, entry_type, net_amount, symbol=None, source="manual", notes=None, conn=None,
):
    insert_dividends_bulk(
        [{"trade_date": trade_date, "symbol": symbol, "entry_type": entry_type,
          "net_amount": net_amount, "source": source, "notes": notes}],
        conn=conn,
    )


def insert_dividends_bulk(rows: list, conn=None):
    """One transaction for the whole batch -- used by the Record Dividend grid
    (Step 6, several rows entered in one sitting) and by the seed script."""
    c, should_close = _with_connection(conn)
    try:
        placeholders = ",".join("?" for _ in DIVIDEND_COLUMNS)
        c.executemany(
            f"INSERT INTO dividends ({','.join(DIVIDEND_COLUMNS)}) VALUES ({placeholders})",
            [[row.get(col) for col in DIVIDEND_COLUMNS] for row in rows],
        )
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        if should_close:
            c.close()


def fetch_trades(conn=None):
    """Returns a DataFrame shaped exactly like calculations.py::compute_realized_pl
    (and compute_fifo_realized_pl) expect: Symbol, Trade Date, Entry Type, Side,
    Quantity, Price, Amount, Commission, Month -- so either function can consume
    this directly with no further reshaping."""
    import pandas as pd

    c, should_close = _with_connection(conn)
    df = pd.read_sql_query("SELECT * FROM trades ORDER BY trade_date", c)
    if should_close:
        c.close()
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df = df.rename(columns={
        "trade_date": "Trade Date", "entry_type": "Entry Type", "side": "Side",
        "symbol": "Symbol", "quantity": "Quantity", "price": "Price",
        "amount": "Amount", "commission": "Commission",
    })
    df["Month"] = df["Trade Date"].dt.to_period("M").dt.to_timestamp() if not df.empty else df["Trade Date"]
    return df


def fetch_dividends(conn=None):
    """Returns a DataFrame shaped like the xlsx Income sheet (Symbol, Trade
    Date, Entry Type, Net Amt) so calculations.py::blended_dividends can
    concatenate this directly with the xlsx side -- see that function's
    docstring for the Entry Type vocabulary difference callers must handle."""
    import pandas as pd

    c, should_close = _with_connection(conn)
    df = pd.read_sql_query("SELECT * FROM dividends ORDER BY trade_date", c)
    if should_close:
        c.close()
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df = df.rename(columns={
        "trade_date": "Trade Date", "symbol": "Symbol", "entry_type": "Entry Type", "net_amount": "Net Amt",
    })
    return df


def set_symbol_type(symbol, allocation_type, conn=None):
    """Upserts a symbol's allocation type. `allocation_type` must be
    'Dividend' or 'Growth' (CHECK-constrained) -- returning a symbol to the
    default is clear_symbol_type(), not set_symbol_type(symbol, "Others"),
    since "Others" is never itself a stored value (see fetch_symbol_types)."""
    c, should_close = _with_connection(conn)
    c.execute(
        "INSERT INTO symbol_types (symbol, allocation_type, updated_at) VALUES (?, ?, datetime('now')) "
        "ON CONFLICT(symbol) DO UPDATE SET allocation_type=excluded.allocation_type, updated_at=excluded.updated_at",
        (symbol, allocation_type),
    )
    c.commit()
    if should_close:
        c.close()


def clear_symbol_type(symbol, conn=None):
    """Deletes the row, returning the symbol to the "Others" default. An
    unknown symbol is a no-op, matching delete_trade()'s convention."""
    c, should_close = _with_connection(conn)
    c.execute("DELETE FROM symbol_types WHERE symbol=?", (symbol,))
    c.commit()
    if should_close:
        c.close()


def fetch_symbol_types(conn=None):
    """Returns a DataFrame (Symbol, Allocation Type) covering EVERY symbol
    that's ever appeared in trades -- not just symbols someone has actively
    classified. A symbol with no symbol_types row (including one that's
    been fully bought and sold -- a real, common case, not an edge case --
    see tests/test_db.py) defaults to "Others". This is the one function
    any caller (the Allocation Type page, the Dashboard, a future rebalance
    planner) should use to get a complete, always-classified view."""
    import pandas as pd

    trades = fetch_trades(conn=conn)
    # dtype="object" is deliberate: pd.DataFrame({"Symbol": []}) built from a plain
    # (possibly empty) Python list otherwise defaults to float64 when there are no
    # trades yet, which then fails to merge against types["Symbol"] (object) below --
    # the same empty-collection dtype-inference pitfall documented elsewhere in core/.
    all_symbols = pd.DataFrame({"Symbol": pd.Series(sorted(trades["Symbol"].dropna().unique()), dtype="object")})

    c, should_close = _with_connection(conn)
    types = pd.read_sql_query("SELECT symbol, allocation_type FROM symbol_types", c)
    if should_close:
        c.close()
    types = types.rename(columns={"symbol": "Symbol", "allocation_type": "Allocation Type"})

    merged = all_symbols.merge(types, on="Symbol", how="left")
    merged["Allocation Type"] = merged["Allocation Type"].fillna("Others")
    return merged


def fetch_unreconciled_trades(cutoff, conn=None):
    """Trades within an official (<=cutoff) statement period that haven't been matched
    against the xlsx yet -- reconciliation candidates. Built on fetch_trades() rather
    than a raw query so callers get the same renamed/typed shape every other page
    already works with."""
    df = fetch_trades(conn=conn)
    if df.empty:
        return df
    return df[(df["Trade Date"] <= cutoff) & df["reconciled_month"].isna()].reset_index(drop=True)


def fetch_unreconciled_dividends(cutoff, conn=None):
    """Dividend-side counterpart to fetch_unreconciled_trades()."""
    df = fetch_dividends(conn=conn)
    if df.empty:
        return df
    return df[(df["Trade Date"] <= cutoff) & df["reconciled_month"].isna()].reset_index(drop=True)


def mark_reconciled(table, row_id, month, conn=None):
    """Sets reconciled_month on a single row. See mark_reconciled_bulk() for the
    table/month contract -- this is just the one-row convenience wrapper."""
    mark_reconciled_bulk(table, [row_id], month, conn=conn)


_RECONCILABLE_TABLES = {"trades", "dividends"}


def mark_reconciled_bulk(table, ids, month, conn=None):
    """One transaction for the whole batch -- mirrors insert_trades_bulk/
    insert_dividends_bulk's pattern. `table` must be 'trades' or 'dividends'
    (validated against a fixed allowlist since table names can't be parameterized
    via '?'). `month` is the xlsx statement Month the row matched against ('%Y-%m'),
    not the cutoff date, so it's traceable to which statement actually covered it."""
    if table not in _RECONCILABLE_TABLES:
        raise ValueError(f"table must be one of {sorted(_RECONCILABLE_TABLES)}, got {table!r}")
    c, should_close = _with_connection(conn)
    try:
        c.executemany(f"UPDATE {table} SET reconciled_month=? WHERE id=?", [(month, i) for i in ids])
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        if should_close:
            c.close()


def count_seed_rows(conn=None):
    c, should_close = _with_connection(conn)
    n = c.execute("SELECT COUNT(*) FROM trades WHERE source='seed'").fetchone()[0]
    if should_close:
        c.close()
    return n


def delete_seed_rows(conn=None):
    c, should_close = _with_connection(conn)
    c.execute("DELETE FROM trades WHERE source='seed'")
    c.execute("DELETE FROM dividends WHERE source='seed'")
    c.commit()
    if should_close:
        c.close()


def delete_trade(trade_id, conn=None):
    """Deletes one trade by id -- the Record Trade page's per-row delete
    button. No source restriction here (unlike delete_trades_by_source):
    the page only ever shows manual/slip rows to delete from, so the
    restriction is enforced by what's displayed, not by this function."""
    c, should_close = _with_connection(conn)
    c.execute("DELETE FROM trades WHERE id=?", (trade_id,))
    c.commit()
    if should_close:
        c.close()


def delete_dividend(dividend_id, conn=None):
    """Deletes one dividend by id -- the Record Dividend page's per-row delete
    button. Mirrors delete_trade(): no source restriction here, since the
    page only ever shows/deletes manual rows, enforced by what's displayed."""
    c, should_close = _with_connection(conn)
    c.execute("DELETE FROM dividends WHERE id=?", (dividend_id,))
    c.commit()
    if should_close:
        c.close()


def delete_trades_by_source(source, conn=None):
    """E.g. delete_trades_by_source("manual") to clear test entries made while
    trying out Record Trade, without touching seeded history or other sources."""
    c, should_close = _with_connection(conn)
    c.execute("DELETE FROM trades WHERE source=?", (source,))
    c.commit()
    if should_close:
        c.close()


def delete_dividends_by_source(source, conn=None):
    c, should_close = _with_connection(conn)
    c.execute("DELETE FROM dividends WHERE source=?", (source,))
    c.commit()
    if should_close:
        c.close()
