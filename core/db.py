"""Turso (SQLite-compatible) persistence for user-entered trades and dividends.

Pure logic, no Streamlit import -- every function accepts an optional
injected `conn` so tests can pass an in-memory sqlite3 database instead of
the real Turso connection (see tests/test_db.py). Tests never go through
get_connection(), so they're unaffected by which backend it targets.
"""

import os

import libsql

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# No longer the live connection target (see get_connection() below) -- kept as the
# path core/backup.py's local snapshot backups still read from, and for the
# regression test that pins it to the project root, not core/'s own directory.
DB_PATH = os.path.join(PROJECT_ROOT, "data", "portfolio.db")

# A list of individual statements, not one big executescript() string -- libsql's
# Python client isn't confirmed to support executescript(), so init_db() below
# loops and .execute()s each one instead, which is guaranteed to work.
SCHEMA_STATEMENTS = [
    """
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
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_trades_symbol_date ON trades(symbol, trade_date)",
    """
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
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_dividends_symbol_date ON dividends(symbol, trade_date)",
    """
    CREATE TABLE IF NOT EXISTS symbol_types (
        symbol           TEXT PRIMARY KEY,
        allocation_type  TEXT NOT NULL CHECK(allocation_type IN ('Dividend', 'Growth')),
        updated_at       TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS rebalance_plans (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        amount        REAL NOT NULL DEFAULT 0,
        created_at    TEXT NOT NULL DEFAULT (datetime('now')),
        completed_at  TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS rebalance_plan_items (
        plan_id  INTEGER NOT NULL REFERENCES rebalance_plans(id),
        symbol   TEXT NOT NULL,
        pct      REAL NOT NULL DEFAULT 0,
        bought   INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (plan_id, symbol)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS trendline_levels (
        symbol      TEXT PRIMARY KEY,
        s3          REAL NOT NULL,
        s2          REAL NOT NULL,
        s1          REAL NOT NULL,
        pivot       REAL NOT NULL,
        r1          REAL NOT NULL,
        r2          REAL NOT NULL,
        r3          REAL NOT NULL,
        is_override INTEGER NOT NULL DEFAULT 0,
        updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS reference_lines (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol            TEXT NOT NULL,
        price             REAL NOT NULL,
        is_override       INTEGER NOT NULL DEFAULT 0,
        captured_side     TEXT,
        passed_at         TEXT,
        captured_timeline TEXT,
        captured_interval TEXT,
        updated_at        TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
]

# reference_lines shipped in v4.4 without captured_side/passed_at, added in v4.4.1 for
# the Monitor Stocks summary tab's "nearest line, freeze on passed" feature. CREATE TABLE
# IF NOT EXISTS above is a no-op against a table that already exists on Turso -- it won't
# retrofit these two new columns onto real, already-shipped rows -- so init_db() also runs
# this small migration, guarded by PRAGMA table_info() (SQLite has no ADD COLUMN IF NOT
# EXISTS) so it's safe to call on every startup, whether the columns already exist or not.
_REFERENCE_LINES_MIGRATION_COLUMNS = {
    "captured_side": "ALTER TABLE reference_lines ADD COLUMN captured_side TEXT",
    "passed_at": "ALTER TABLE reference_lines ADD COLUMN passed_at TEXT",
}


def _migrate_reference_lines_columns(c):
    existing = {row[1] for row in c.execute("PRAGMA table_info(reference_lines)").fetchall()}
    for column, statement in _REFERENCE_LINES_MIGRATION_COLUMNS.items():
        if column not in existing:
            c.execute(statement)

TRADE_COLUMNS = [
    "trade_date", "entry_type", "side", "symbol", "description", "quantity", "price",
    "amount", "commission", "vat", "reserved_fee", "fee_rebate", "order_id", "order_type",
    "source", "notes",
]
DIVIDEND_COLUMNS = ["trade_date", "symbol", "entry_type", "net_amount", "source", "notes"]


def get_connection():
    return libsql.connect(
        database=os.environ["TURSO_DATABASE_URL"],
        auth_token=os.environ["TURSO_AUTH_TOKEN"],
    )


def _with_connection(conn):
    """Returns (conn, should_close). Opens the real Turso connection if none was injected."""
    if conn is not None:
        return conn, False
    return get_connection(), True


def _sql_literal(value):
    """Formats `value` as a safe inline SQL text literal, for the rare case (see
    save_reference_lines) where multiple heterogeneous statements are batched into one
    executescript() call and '?' parameter binding isn't available. None becomes SQL
    NULL; anything else is stringified and single-quotes are escaped by doubling them,
    the standard SQL-text escaping rule."""
    if value is None:
        return "NULL"
    return "'" + str(value).replace("'", "''") + "'"


def _read_sql(c, query):
    """Manual fetch instead of pd.read_sql_query(query, c) -- pandas only special-cases
    a stdlib sqlite3.Connection or a SQLAlchemy connectable, and a libsql.Connection is
    neither, so this stays portable across both the real Turso connection and the
    in-memory sqlite3 connections tests inject."""
    import pandas as pd

    cur = c.execute(query)
    columns = [d[0] for d in cur.description]
    return pd.DataFrame(cur.fetchall(), columns=columns)


def init_db(conn=None):
    c, should_close = _with_connection(conn)
    for statement in SCHEMA_STATEMENTS:
        c.execute(statement)
    _migrate_reference_lines_columns(c)
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
    df = _read_sql(c, "SELECT * FROM trades ORDER BY trade_date")
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
    df = _read_sql(c, "SELECT * FROM dividends ORDER BY trade_date")
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
    types = _read_sql(c, "SELECT symbol, allocation_type FROM symbol_types")
    if should_close:
        c.close()
    types = types.rename(columns={"symbol": "Symbol", "allocation_type": "Allocation Type"})

    merged = all_symbols.merge(types, on="Symbol", how="left")
    merged["Allocation Type"] = merged["Allocation Type"].fillna("Others")
    return merged


def save_trendline_levels(symbol, levels: dict, *, is_override: bool = False, conn=None):
    """Upserts a symbol's Pivot Point levels (S3/S2/S1/Pivot/R1/R2/R3), same
    upsert-one-row-per-symbol shape as set_symbol_type() above. `is_override`
    distinguishes "auto-calculated, last seen" (False) from "user deliberately
    moved/typed this" (True) -- a future notification checker cares which one
    it's watching. Symbol Analysis calls this with is_override=False on every
    meaningful recompute (page load, timeframe/interval change), and
    is_override=True on every drag-end or manual level edit."""
    c, should_close = _with_connection(conn)
    c.execute(
        "INSERT INTO trendline_levels (symbol, s3, s2, s1, pivot, r1, r2, r3, is_override, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now')) "
        "ON CONFLICT(symbol) DO UPDATE SET "
        "s3=excluded.s3, s2=excluded.s2, s1=excluded.s1, pivot=excluded.pivot, "
        "r1=excluded.r1, r2=excluded.r2, r3=excluded.r3, "
        "is_override=excluded.is_override, updated_at=excluded.updated_at",
        (symbol, levels["S3"], levels["S2"], levels["S1"], levels["Pivot"],
         levels["R1"], levels["R2"], levels["R3"], int(is_override)),
    )
    c.commit()
    if should_close:
        c.close()


def fetch_trendline_levels(conn=None):
    """Returns a DataFrame (Symbol, S3, S2, S1, Pivot, R1, R2, R3, Is Override,
    Updated At) for every symbol that's had levels saved. Symbol Analysis uses
    this to seed a symbol's levels on page load instead of always recomputing
    from scratch; a future notification checker would scan this same table
    against live prices. Unlike fetch_symbol_types() above, this does NOT
    default-fill every traded symbol -- a symbol with no saved row just isn't a
    row here, since there's no meaningful default Pivot Point levels the way
    "Others" is a meaningful default Allocation Type."""
    c, should_close = _with_connection(conn)
    df = _read_sql(c, "SELECT symbol, s3, s2, s1, pivot, r1, r2, r3, is_override, updated_at FROM trendline_levels")
    if should_close:
        c.close()
    return df.rename(columns={
        "symbol": "Symbol", "s3": "S3", "s2": "S2", "s1": "S1", "pivot": "Pivot",
        "r1": "R1", "r2": "R2", "r3": "R3", "is_override": "Is Override", "updated_at": "Updated At",
    })


def save_reference_lines(symbol, lines: list, *, latest_price: float, captured_timeline=None, captured_interval=None, conn=None):
    """Replaces a symbol's whole captured set of Reference Lines -- delete-then-insert-all
    scoped to `symbol`, the simplest way to sync a variable-length list (unlike Pivot
    Points' fixed 7 columns, there's no fixed set of column names to upsert against).
    Called on every Regenerate/drag/delete/add, never on a bare Timeline/Interval switch --
    Reference Lines are captured at a moment, not recomputed on navigation.

    `lines` is a list of {"price": float, "is_override": bool} dicts. `captured_timeline`/
    `captured_interval` describe what basis produced this set (shown back to the user as a
    caption) -- informational only, not part of what identifies the set; left None for a
    manually-added line that wasn't part of a Regenerate call.

    `latest_price` is used to derive each line's `captured_side` ('resistance' if the price
    was at/above `latest_price` at save time, else 'support') -- a permanent, immutable
    record of which side a line was captured on, distinct from the per-symbol chart's own
    "derive side live from CURRENT price" rule (that rule is unchanged, still used for chart
    coloring). `captured_side` is what the Monitor Stocks summary tab (v4.4.1) uses to know
    which direction counts as "passed" for a given line, via mark_reference_lines_passed().
    Every call here -- Regenerate, drag, delete, or add -- freshly re-derives captured_side
    and resets `passed_at` to NULL for the whole set (a fresh INSERT never sets it), on the
    reasoning that any edit on a symbol's own page counts as fresh engagement with it.

    Uses executescript() (one DELETE + N INSERTs sent as a single multi-statement batch)
    rather than separate execute()/executemany() calls -- a real, measured difference
    against the remote Turso connection this app uses (each call pays its own network
    round trip): two separate calls took ~1.6s, executescript's single round trip took
    ~500ms, matching set_symbol_type/save_trendline_levels' single-upsert speed. This
    matters because this function runs synchronously on every drag/delete/add/Regenerate,
    inside the same @st.fragment rerun the chart redraws in -- the extra ~1s was directly
    visible as the whole toolbar hanging before the fragment finished re-rendering.
    executescript() doesn't support '?' parameter binding across statements, so values are
    inlined as escaped SQL literals instead (see _sql_literal below) -- safe here since
    every value is either a float/int the caller computed, or one of a small fixed set of
    internal Timeline/Interval labels, never freeform user text."""
    c, should_close = _with_connection(conn)
    statements = [f"DELETE FROM reference_lines WHERE symbol={_sql_literal(symbol)};"]
    for line in lines:
        captured_side = "resistance" if float(line["price"]) >= latest_price else "support"
        statements.append(
            "INSERT INTO reference_lines "
            "(symbol, price, is_override, captured_side, captured_timeline, captured_interval, updated_at) "
            f"VALUES ({_sql_literal(symbol)}, {float(line['price'])!r}, {int(line['is_override'])}, "
            f"{_sql_literal(captured_side)}, {_sql_literal(captured_timeline)}, {_sql_literal(captured_interval)}, "
            "datetime('now'));"
        )
    try:
        c.executescript("\n".join(statements))
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        if should_close:
            c.close()


def fetch_reference_lines(conn=None):
    """Returns a DataFrame (Symbol, Price, Is Override, Captured Side, Passed At, Captured
    Timeline, Captured Interval, Updated At) -- one row per currently-captured Reference
    Line, across every symbol that has any. Symbol Analysis uses this to load a symbol's
    last-captured set on a fresh session (instead of a blank chart); Monitor Stocks'
    Reference Lines summary tab (v4.4.1) uses Captured Side/Passed At to build its
    "nearest, or frozen passed" cell per symbol. A symbol with nothing captured yet just
    isn't present here."""
    c, should_close = _with_connection(conn)
    df = _read_sql(
        c, "SELECT symbol, price, is_override, captured_side, passed_at, captured_timeline, "
        "captured_interval, updated_at FROM reference_lines",
    )
    if should_close:
        c.close()
    return df.rename(columns={
        "symbol": "Symbol", "price": "Price", "is_override": "Is Override",
        "captured_side": "Captured Side", "passed_at": "Passed At",
        "captured_timeline": "Captured Timeline", "captured_interval": "Captured Interval",
        "updated_at": "Updated At",
    })


def mark_reference_lines_passed(latest_prices: dict, conn=None):
    """For every captured Reference Line not yet marked passed (`passed_at IS NULL`),
    checks whether the symbol's CURRENT price (from `latest_prices`, a {symbol: price}
    dict -- Monitor Stocks already has this in memory from its own profile fetch, so this
    costs no extra network call) has reached/crossed that line's captured price, using
    `captured_side` to know which direction counts as a cross: 'resistance' passes when
    price has risen to/above it, 'support' passes when price has fallen to/below it. Sets
    `passed_at` to today's date (`date('now')`) for whatever newly qualifies, once --
    already-passed rows and symbols missing from `latest_prices` (unresolved/not yet
    fetched) are left untouched. Returns the number of rows newly marked passed, mainly
    for tests/logging.

    Self-healing backfill for legacy rows: `captured_side` didn't exist before v4.4.1, so
    any row saved before this shipped has `captured_side IS NULL` -- left as-is, such a
    row would never match either side and would silently be excluded from both the
    Monitor Stocks summary tab's cells AND this passed-check, forever (a real bug this
    fixed after being caught live: several already-captured symbols showed "-" on both
    sides post-migration). Fixed by deriving it here, the first time such a row is seen,
    from CURRENT price vs. the row's own captured price -- the same rule a fresh capture
    uses, just approximated against today's price instead of the (unrecorded) price at
    the row's original save time, since that's the best information available for
    genuinely old data.

    Deliberately id-based single-row UPDATEs, not a bulk statement -- this runs against a
    small, usually-empty candidate set (only rows with passed_at IS NULL, and only those
    whose price was actually just crossed), not the whole table, so the simplicity of one
    UPDATE per row outweighs the round-trip cost a bulk rewrite would save here."""
    import pandas as pd

    c, should_close = _with_connection(conn)
    candidates = _read_sql(
        c, "SELECT id, symbol, price, captured_side FROM reference_lines WHERE passed_at IS NULL",
    )
    newly_passed = 0
    try:
        for row in candidates.itertuples():
            price = latest_prices.get(row.symbol)
            if price is None or pd.isna(price):
                continue
            captured_side = row.captured_side
            if captured_side not in ("resistance", "support"):
                # Same rule save_reference_lines uses for a fresh capture: the LINE's own
                # price at/above current price -> resistance, below -> support. (Caught a
                # real bug here during testing: an earlier draft compared the operands in
                # the wrong order, backfilling every legacy row to the opposite side.)
                captured_side = "resistance" if row.price >= price else "support"
                c.execute(
                    f"UPDATE reference_lines SET captured_side={_sql_literal(captured_side)} "
                    f"WHERE id={int(row.id)}",
                )
            crossed = (
                (captured_side == "resistance" and price >= row.price)
                or (captured_side == "support" and price <= row.price)
            )
            if crossed:
                c.execute(
                    f"UPDATE reference_lines SET passed_at = date('now') WHERE id = {int(row.id)}",
                )
                newly_passed += 1
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        if should_close:
            c.close()
    return newly_passed


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


def get_active_rebalance_plan(conn=None):
    """Returns the current in-progress plan (completed_at IS NULL) as
    {id, amount, items: {symbol: {pct, bought}}}, or None if no plan is
    active -- either never started, or the previous one was fully
    completed/reset. Callers use None as the signal to start a fresh one
    via start_rebalance_plan()."""
    c, should_close = _with_connection(conn)
    plan_row = c.execute(
        "SELECT id, amount FROM rebalance_plans WHERE completed_at IS NULL ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if plan_row is None:
        if should_close:
            c.close()
        return None
    plan_id, amount = plan_row
    items = c.execute(
        "SELECT symbol, pct, bought FROM rebalance_plan_items WHERE plan_id=?", (plan_id,)
    ).fetchall()
    if should_close:
        c.close()
    return {
        "id": plan_id,
        "amount": amount,
        "items": {symbol: {"pct": pct, "bought": bool(bought)} for symbol, pct, bought in items},
    }


def start_rebalance_plan(symbols, conn=None):
    """Creates a new active plan (amount=0) with one item row per symbol
    (pct=0, bought=0). Caller is responsible for only invoking this when
    get_active_rebalance_plan() returned None -- this function doesn't
    check that itself, so calling it while a plan is already active would
    create two concurrently-active plans and break the "one active plan"
    assumption get_active_rebalance_plan() relies on. Returns the new
    plan's id."""
    c, should_close = _with_connection(conn)
    try:
        cur = c.execute("INSERT INTO rebalance_plans (amount, created_at) VALUES (0, datetime('now'))")
        plan_id = cur.lastrowid
        c.executemany(
            "INSERT INTO rebalance_plan_items (plan_id, symbol, pct, bought) VALUES (?, ?, 0, 0)",
            [(plan_id, s) for s in symbols],
        )
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        if should_close:
            c.close()
    return plan_id


def update_rebalance_plan_amount(plan_id, amount, conn=None):
    c, should_close = _with_connection(conn)
    c.execute("UPDATE rebalance_plans SET amount=? WHERE id=?", (amount, plan_id))
    c.commit()
    if should_close:
        c.close()


def update_rebalance_plan_item(plan_id, symbol, *, pct=None, bought=None, conn=None):
    """Partial update -- only the given field(s) change. After writing,
    checks whether every item on this plan is now bought, and if so stamps
    completed_at (auto-complete, matching the "plan clears once everything's
    ticked" requirement) -- ticking the last box is itself what retires the
    plan, no separate finish action needed. A plan with zero items never
    auto-completes this way (nothing to trigger the check), which is fine:
    an empty plan (no Dividend-classified holdings yet) just sits idle
    until reset_rebalance_plan() or real holdings appear."""
    c, should_close = _with_connection(conn)
    try:
        if pct is not None:
            c.execute("UPDATE rebalance_plan_items SET pct=? WHERE plan_id=? AND symbol=?", (pct, plan_id, symbol))
        if bought is not None:
            c.execute(
                "UPDATE rebalance_plan_items SET bought=? WHERE plan_id=? AND symbol=?",
                (1 if bought else 0, plan_id, symbol),
            )
        remaining = c.execute(
            "SELECT COUNT(*) FROM rebalance_plan_items WHERE plan_id=? AND bought=0", (plan_id,)
        ).fetchone()[0]
        if remaining == 0:
            c.execute("UPDATE rebalance_plans SET completed_at=datetime('now') WHERE id=?", (plan_id,))
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        if should_close:
            c.close()


def reset_rebalance_plan(plan_id, conn=None):
    """Manually abandons a plan (stamps completed_at) without requiring
    every item to be bought first -- the "Reset plan" button's code path,
    distinct from update_rebalance_plan_item()'s auto-complete-on-all-bought."""
    c, should_close = _with_connection(conn)
    c.execute("UPDATE rebalance_plans SET completed_at=datetime('now') WHERE id=?", (plan_id,))
    c.commit()
    if should_close:
        c.close()
