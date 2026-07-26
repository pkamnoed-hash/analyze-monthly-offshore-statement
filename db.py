"""SQLite persistence for user-entered trades and dividends.

Pure logic, no Streamlit import -- every function accepts an optional
injected `conn` so tests can pass an in-memory database instead of the real
file (see tests/test_db.py).
"""

import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "portfolio.db")

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
    import pandas as pd

    c, should_close = _with_connection(conn)
    df = pd.read_sql_query("SELECT * FROM dividends ORDER BY trade_date", c)
    if should_close:
        c.close()
    if not df.empty:
        df["trade_date"] = pd.to_datetime(df["trade_date"])
    return df


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
