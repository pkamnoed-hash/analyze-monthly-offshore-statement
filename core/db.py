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
        allocation_type  TEXT NOT NULL CHECK(allocation_type NOT IN ('Others', '')),
        updated_at       TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS target_allocation_categories (
        category    TEXT PRIMARY KEY,
        target_pct  REAL NOT NULL DEFAULT 0,
        updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS target_allocation_sectors (
        category    TEXT NOT NULL,
        sector      TEXT NOT NULL,
        target_pct  REAL NOT NULL DEFAULT 0 CHECK(target_pct >= 0 AND target_pct <= 100),
        updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
        PRIMARY KEY (category, sector)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS target_allocations (
        symbol      TEXT PRIMARY KEY,
        target_pct  REAL NOT NULL DEFAULT 0 CHECK(target_pct >= 0 AND target_pct <= 100),
        updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
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
    """
    CREATE TABLE IF NOT EXISTS market_profile_cache (
        symbol              TEXT PRIMARY KEY,
        description         TEXT,
        sector              TEXT,
        industry            TEXT,
        quote_type          TEXT,
        beta                REAL,
        latest_price        REAL,
        high_90d            REAL,
        low_90d             REAL,
        dividend_per_year   REAL,
        dividend_yield_pct  REAL,
        dividend_frequency  TEXT,
        ex_date             TEXT,
        history_90d         TEXT,
        fetched_at          TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS webauthn_credentials (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        credential_id TEXT NOT NULL UNIQUE,
        public_key    TEXT NOT NULL,
        sign_count    INTEGER NOT NULL DEFAULT 0,
        device_label  TEXT,
        created_at    TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    # v4.7 -- CHECK (id = 1) is a first use in this file: every other table can hold
    # many rows, but this one gates login itself, so a stray second row must fail
    # loudly at the DB level rather than leave it ambiguous which row is "current."
    # Absence of a row (a fresh deploy that's never had its password changed) is
    # handled by callers falling back to st.secrets, not by seeding a default row here.
    """
    CREATE TABLE IF NOT EXISTS app_password (
        id            INTEGER PRIMARY KEY CHECK (id = 1),
        salt          TEXT NOT NULL,
        password_hash TEXT NOT NULL,
        updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
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


def _migrate_symbol_types_open_category(c):
    """symbol_types.allocation_type was CHECK-constrained to exactly ('Dividend',
    'Growth') in V2.1. Relaxed (Target Allocation Tracker) to accept any category
    except the reserved 'Others' sentinel, so categories beyond Growth/Dividend can
    exist app-wide without another schema migration later. SQLite can't ALTER a CHECK
    constraint in place, so this recreates the table -- rename old, create new with the
    relaxed constraint, copy rows across, drop old. Detects the old constraint via
    sqlite_master's stored SQL text; a no-op once already migrated (idempotent, safe to
    run on every init_db() call, same convention as _migrate_reference_lines_columns)."""
    row = c.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='symbol_types'"
    ).fetchone()
    if row is None or "IN ('Dividend', 'Growth')" not in (row[0] or ""):
        return
    c.execute("ALTER TABLE symbol_types RENAME TO symbol_types_old")
    c.execute(
        "CREATE TABLE symbol_types ("
        "symbol TEXT PRIMARY KEY, "
        "allocation_type TEXT NOT NULL CHECK(allocation_type NOT IN ('Others', '')), "
        "updated_at TEXT NOT NULL DEFAULT (datetime('now')))"
    )
    c.execute("INSERT INTO symbol_types SELECT * FROM symbol_types_old")
    c.execute("DROP TABLE symbol_types_old")


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
    _migrate_symbol_types_open_category(c)
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
    """Upserts a symbol's allocation type. `allocation_type` can be any
    non-empty category except 'Others' (CHECK-constrained -- open-ended
    since the Target Allocation Tracker, previously a fixed Dividend/Growth
    enum) -- returning a symbol to the default is clear_symbol_type(), not
    set_symbol_type(symbol, "Others"), since "Others" is never itself a
    stored value (see fetch_symbol_types)."""
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


def set_target_category_pct(category, target_pct, conn=None):
    """Upserts one category's target %. `category` is free text -- any value
    already present in symbol_types.allocation_type, including 'Others'
    (unlike symbol_types itself, 'Others' is a perfectly legitimate stored
    target here; nothing reserves it)."""
    c, should_close = _with_connection(conn)
    c.execute(
        "INSERT INTO target_allocation_categories (category, target_pct, updated_at) "
        "VALUES (?, ?, datetime('now')) ON CONFLICT(category) DO UPDATE "
        "SET target_pct=excluded.target_pct, updated_at=excluded.updated_at",
        (category, target_pct),
    )
    c.commit()
    if should_close:
        c.close()


def fetch_target_categories(conn=None):
    """Returns a DataFrame (Category, Target %) of whatever category targets
    have actually been set -- no synthetic full-coverage fill at this layer,
    unlike fetch_symbol_types()/fetch_target_allocations(). Reason: the
    universe of "real" categories depends on live symbol_types + current
    holdings data, which this function has no access to -- core/
    target_allocation.py fills the 0.0 default once it has that context."""
    c, should_close = _with_connection(conn)
    rows = _read_sql(c, "SELECT category, target_pct FROM target_allocation_categories")
    if should_close:
        c.close()
    return rows.rename(columns={"category": "Category", "target_pct": "Target %"})


def set_target_sector_pct(category, sector, target_pct, conn=None):
    """Upserts one (category, sector) pair's target %. Both are free text --
    `sector` is whatever Classification (Sector for equities, Industry
    otherwise -- see core/rebalance.py's get_dividend_holdings()) currently
    reads for a held stock; not constrained to a fixed list since Yahoo
    Finance's own sector/industry vocabulary isn't fixed or known in advance."""
    c, should_close = _with_connection(conn)
    c.execute(
        "INSERT INTO target_allocation_sectors (category, sector, target_pct, updated_at) "
        "VALUES (?, ?, ?, datetime('now')) ON CONFLICT(category, sector) DO UPDATE "
        "SET target_pct=excluded.target_pct, updated_at=excluded.updated_at",
        (category, sector, target_pct),
    )
    c.commit()
    if should_close:
        c.close()


def clear_target_sector_pct(category, sector, conn=None):
    """Deletes the row. An unknown (category, sector) pair is a no-op,
    matching clear_symbol_type()'s convention."""
    c, should_close = _with_connection(conn)
    c.execute(
        "DELETE FROM target_allocation_sectors WHERE category=? AND sector=?",
        (category, sector),
    )
    c.commit()
    if should_close:
        c.close()


def fetch_target_sectors(conn=None):
    """Returns a DataFrame (Category, Sector, Target %) of whatever (category,
    sector) targets have actually been set -- same no-synthetic-fill reasoning
    as fetch_target_categories()."""
    c, should_close = _with_connection(conn)
    rows = _read_sql(c, "SELECT category, sector, target_pct FROM target_allocation_sectors")
    if should_close:
        c.close()
    return rows.rename(columns={"category": "Category", "sector": "Sector", "target_pct": "Target %"})


def set_target_allocation(symbol, target_pct, conn=None):
    """Upserts one symbol's target %. `target_pct` must be 0-100
    (CHECK-constrained) -- unlike symbol_types, 0.0 is a normal storable
    value here (no reserved-default concept); clear_target_allocation()
    below is a convenience for removing the row entirely, functionally
    identical to set_target_allocation(symbol, 0.0) as far as
    fetch_target_allocations() is concerned."""
    c, should_close = _with_connection(conn)
    c.execute(
        "INSERT INTO target_allocations (symbol, target_pct, updated_at) VALUES (?, ?, datetime('now')) "
        "ON CONFLICT(symbol) DO UPDATE SET target_pct=excluded.target_pct, updated_at=excluded.updated_at",
        (symbol, target_pct),
    )
    c.commit()
    if should_close:
        c.close()


def clear_target_allocation(symbol, conn=None):
    """Deletes the row, returning the symbol to the 0.0 default. Unknown
    symbol is a no-op, matching clear_symbol_type()'s convention."""
    c, should_close = _with_connection(conn)
    c.execute("DELETE FROM target_allocations WHERE symbol=?", (symbol,))
    c.commit()
    if should_close:
        c.close()


def fetch_target_allocations(conn=None):
    """Returns a DataFrame (Symbol, Target %) covering EVERY symbol that's
    ever appeared in trades -- not just symbols someone has actively
    targeted. A symbol with no target_allocations row defaults to Target %
    = 0.0. Same full-coverage-at-fetch-time convention as
    fetch_symbol_types() -- a plain, DB-derivable universe (every ever-traded
    symbol), unlike fetch_target_categories()/fetch_target_sectors()."""
    import pandas as pd

    trades = fetch_trades(conn=conn)
    all_symbols = pd.DataFrame({"Symbol": pd.Series(sorted(trades["Symbol"].dropna().unique()), dtype="object")})

    c, should_close = _with_connection(conn)
    rows = _read_sql(c, "SELECT symbol, target_pct FROM target_allocations")
    if should_close:
        c.close()
    rows = rows.rename(columns={"symbol": "Symbol", "target_pct": "Target %"})

    merged = all_symbols.merge(rows, on="Symbol", how="left")
    merged["Target %"] = merged["Target %"].fillna(0.0)
    return merged


def save_reference_lines(symbol, lines: list, *, latest_price: float, captured_timeline=None, captured_interval=None, conn=None):
    """Replaces a symbol's whole captured set of Reference Lines -- delete-then-insert-all
    scoped to `symbol`, the simplest way to sync a variable-length list (unlike a
    fixed-shape upsert such as set_symbol_type(), there's no fixed set of column names to
    upsert against). Called on every Regenerate/drag/delete/add, never on a bare Timeline/Interval switch --
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
    ~500ms, matching set_symbol_type's own single-upsert speed. This
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


def save_market_profile_cache(rows: list[dict], conn=None):
    """Upserts one row per symbol into market_profile_cache -- v4.5's durable fallback
    for fetch_stock_profile(): when a live yfinance fetch fails (e.g. Yahoo
    rate-limiting Streamlit Community Cloud's shared IP, the real incident that
    prompted this), the caller falls back to whatever was last captured here instead
    of showing a blank row. `rows` matches fetch_stock_profile()'s own successful-row
    shape (Symbol/Description/Sector/Industry/Quote Type/Beta/History90D/Latest
    Price/High90D/Low90D/Dividend Per Year/Dividend Yield %/Dividend Frequency/
    Ex-Date) -- only ever called with rows that DID succeed live, never the
    failure-shape (None/NaN) rows, so the cache only ever holds real data, never a
    good row overwritten by a blank one.

    INSERT OR REPLACE (not a separate UPDATE/INSERT branch) since `symbol` is the
    table's PRIMARY KEY -- one row per symbol, always. Batched into a single
    executescript() call, same reasoning as save_reference_lines -- one Turso round
    trip instead of one per symbol. history_90d is JSON-encoded (no native SQLite
    array type); Beta/Latest Price/etc. can be real NaN even on a successful fetch
    (e.g. Beta when yfinance has neither `beta` nor `beta3Year`), handled explicitly
    since a bare Python `nan` isn't valid inline SQL."""
    import json

    import pandas as pd

    def _num(value):
        return "NULL" if pd.isna(value) else repr(float(value))

    c, should_close = _with_connection(conn)
    statements = []
    for row in rows:
        ex_date = row.get("Ex-Date")
        ex_date_str = ex_date.strftime("%Y-%m-%d") if pd.notna(ex_date) else None
        statements.append(
            "INSERT OR REPLACE INTO market_profile_cache "
            "(symbol, description, sector, industry, quote_type, beta, latest_price, "
            "high_90d, low_90d, dividend_per_year, dividend_yield_pct, dividend_frequency, "
            "ex_date, history_90d, fetched_at) VALUES ("
            f"{_sql_literal(row['Symbol'])}, {_sql_literal(row.get('Description'))}, "
            f"{_sql_literal(row.get('Sector'))}, {_sql_literal(row.get('Industry'))}, "
            f"{_sql_literal(row.get('Quote Type'))}, {_num(row.get('Beta'))}, "
            f"{_num(row.get('Latest Price'))}, {_num(row.get('High90D'))}, {_num(row.get('Low90D'))}, "
            f"{_num(row.get('Dividend Per Year'))}, {_num(row.get('Dividend Yield %'))}, "
            f"{_sql_literal(row.get('Dividend Frequency'))}, {_sql_literal(ex_date_str)}, "
            f"{_sql_literal(json.dumps(row.get('History90D') or []))}, datetime('now'));"
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


def fetch_market_profile_cache(conn=None):
    """Returns a DataFrame (Symbol, Description, Sector, Industry, Quote Type, Beta,
    Latest Price, High90D, Low90D, Dividend Per Year, Dividend Yield %, Dividend
    Frequency, Ex-Date, History90D, Fetched At) -- v4.5's durable fallback source for
    Monitor Stocks' _cached_fetch_stock_profile when a live yfinance fetch fails.
    history_90d is JSON-decoded back into a plain list of floats; Ex-Date/Fetched At
    are parsed back into real Timestamps. A symbol never successfully fetched at
    least once just isn't present here -- same "absence means never captured"
    convention fetch_reference_lines already uses."""
    import json

    import pandas as pd

    c, should_close = _with_connection(conn)
    df = _read_sql(
        c, "SELECT symbol, description, sector, industry, quote_type, beta, latest_price, "
        "high_90d, low_90d, dividend_per_year, dividend_yield_pct, dividend_frequency, "
        "ex_date, history_90d, fetched_at FROM market_profile_cache",
    )
    if should_close:
        c.close()
    df = df.rename(columns={
        "symbol": "Symbol", "description": "Description", "sector": "Sector",
        "industry": "Industry", "quote_type": "Quote Type", "beta": "Beta",
        "latest_price": "Latest Price", "high_90d": "High90D", "low_90d": "Low90D",
        "dividend_per_year": "Dividend Per Year", "dividend_yield_pct": "Dividend Yield %",
        "dividend_frequency": "Dividend Frequency", "ex_date": "Ex-Date",
        "history_90d": "History90D", "fetched_at": "Fetched At",
    })
    df["Ex-Date"] = pd.to_datetime(df["Ex-Date"])
    df["Fetched At"] = pd.to_datetime(df["Fetched At"])
    df["History90D"] = df["History90D"].apply(lambda v: json.loads(v) if isinstance(v, str) else [])
    return df


def save_webauthn_credential(credential_id: str, public_key: str, *, device_label: str | None = None, conn=None):
    """v4.6 -- registers one device's WebAuthn credential (Face ID/Touch ID) for
    biometric login. `credential_id`/`public_key` are base64url TEXT, not raw bytes --
    they're already opaque, base64url-encoded strings by the time core/webauthn_auth.py
    hands them here (matching how the `webauthn` library represents them for JSON
    transport), so there's no reason to add a bytes<->text conversion layer on top.

    Always a plain INSERT, never an upsert -- there's no per-user table this app (one
    shared password, no accounts) could key a device against, so every registered
    device just becomes another row that can unlock the same account. A genuine
    re-registration of the same physical device produces a NEW credential_id from the
    platform authenticator (generate_registration_options' exclude_credentials, built
    from fetch_webauthn_credentials() below, steers the OS away from silently reusing
    an old one), so duplicate rows for "the same" device don't happen in practice."""
    c, should_close = _with_connection(conn)
    c.execute(
        "INSERT INTO webauthn_credentials (credential_id, public_key, device_label, created_at) "
        "VALUES (?, ?, ?, datetime('now'))",
        (credential_id, public_key, device_label),
    )
    c.commit()
    if should_close:
        c.close()


def fetch_webauthn_credentials(conn=None):
    """Returns a DataFrame (Id, Credential Id, Public Key, Sign Count, Device Label,
    Created At) -- one row per registered device, empty if biometric login has never
    been set up. dashboard_app.py uses emptiness to decide whether to show the
    "Unlock with Face ID/Touch ID" button at all (no point offering it with nothing
    registered), and uses every row's Credential Id to build
    generate_authentication_options' allow_credentials list."""
    c, should_close = _with_connection(conn)
    df = _read_sql(
        c, "SELECT id, credential_id, public_key, sign_count, device_label, created_at "
        "FROM webauthn_credentials",
    )
    if should_close:
        c.close()
    return df.rename(columns={
        "id": "Id", "credential_id": "Credential Id", "public_key": "Public Key",
        "sign_count": "Sign Count", "device_label": "Device Label", "created_at": "Created At",
    })


def update_webauthn_sign_count(credential_id: str, new_sign_count: int, conn=None):
    """Bumps a credential's stored sign_count after a successful authentication --
    WebAuthn's own replay-attack defense: each authenticator maintains a counter that
    must only ever increase, so an assertion reporting an old-or-equal count (a cloned
    authenticator, or a replayed request) is something core/webauthn_auth.py's
    verify_authentication() already rejects before this function is ever called; by the
    time this runs, `new_sign_count` is already confirmed higher than what was stored."""
    c, should_close = _with_connection(conn)
    c.execute(
        "UPDATE webauthn_credentials SET sign_count = ? WHERE credential_id = ?",
        (new_sign_count, credential_id),
    )
    c.commit()
    if should_close:
        c.close()


def fetch_app_password(conn=None) -> tuple[str, str] | None:
    """v4.7 -- returns (salt, password_hash) from the app_password singleton row, or
    None if the password has never been changed from the app (every deployment starts
    this way). Unlike every other fetch_* here, this returns a plain tuple instead of a
    DataFrame -- it's one scalar fact with no table to show, not UI-facing rows."""
    c, should_close = _with_connection(conn)
    cur = c.execute("SELECT salt, password_hash FROM app_password WHERE id = 1")
    row = cur.fetchone()
    if should_close:
        c.close()
    return (row[0], row[1]) if row else None


def save_app_password(salt: str, password_hash: str, conn=None):
    """v4.7 -- upserts the app_password singleton row (id=1), same INSERT ... ON
    CONFLICT DO UPDATE shape set_symbol_type already uses for its own upsert, just
    keyed on a fixed id instead of a natural key since there's only ever one shared
    password for this whole app."""
    c, should_close = _with_connection(conn)
    c.execute(
        "INSERT INTO app_password (id, salt, password_hash, updated_at) VALUES (1, ?, ?, datetime('now')) "
        "ON CONFLICT(id) DO UPDATE SET salt=excluded.salt, password_hash=excluded.password_hash, "
        "updated_at=excluded.updated_at",
        (salt, password_hash),
    )
    c.commit()
    if should_close:
        c.close()


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
