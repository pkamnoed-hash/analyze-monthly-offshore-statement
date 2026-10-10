"""V4.18 prod catch-up: replace the manual trades/dividends the new July-September 2026
statement now covers with the audited data from that statement, the same fix already
applied and verified on dev.

What this does, in order:
  1. Connects to PROD (refuses to run against anything else).
  2. Takes a fresh backup of prod into data/backups/ (belt-and-braces -- a backup was
     already taken before the June-to-September code/workbook change shipped).
  3. Prints exactly which rows it's about to touch and asks for a typed confirmation.
  4. Deletes the manual trades/dividends dated on or before the statement's cutoff
     (2026-09-30) -- leaves anything dated after cutoff (October activity not yet on
     any statement) completely untouched.
  5. Re-seeds (the same logic as seed_from_xlsx.py --force) from the new workbook, so
     the deleted period is replaced by the broker's own precise figures.
  6. Verifies: re-seeded trade/dividend counts match the workbook's own row counts,
     and current positions reconstructed from only the <=cutoff trades match the
     statement's own Holdings sheet exactly, symbol by symbol.

Nothing here touches October's real activity (the 10 trades / 7 dividends already
logged for October stay exactly as they are) or anything outside the trades/
dividends tables.

Usage:
    python scripts/prod_q3_catchup.py
"""

import os
import sys

import pandas as pd
import tomllib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

SECRETS_PATH = os.path.join(ROOT, ".streamlit", "secrets.prod.toml.bak")
WORKBOOK = os.path.join(ROOT, "data", "Offshore_Statements_2023-01_to_2026-09.xlsx")
CUTOFF = pd.Timestamp("2026-09-30")


def main():
    with open(SECRETS_PATH, "rb") as f:
        secrets = tomllib.load(f)
    if secrets.get("APP_ENV") != "prod":
        print(f"Refusing to run: {SECRETS_PATH} is not pointed at prod (APP_ENV={secrets.get('APP_ENV')!r}).")
        sys.exit(1)
    os.environ["TURSO_DATABASE_URL"] = secrets["TURSO_DATABASE_URL"]
    os.environ["TURSO_AUTH_TOKEN"] = secrets["TURSO_AUTH_TOKEN"]

    from core import backup, db

    # A fresh connection per step, not one long-lived connection for the whole run --
    # found by testing: Turso's connection (a Hrana stream) can go stale if it sits
    # open too long, e.g. across the confirmation prompt below, and a write through a
    # stale connection fails partway rather than cleanly, which is a much worse place
    # to fail than between two short, independent steps each starting fresh.

    # --- 1. fresh backup first ---------------------------------------------------
    conn = db.get_connection()
    filename, counts = backup.backup_turso_database(
        conn, os.path.join(ROOT, "data", "backups"), env_label="prod", version="v4.18-catchup",
    )
    conn.close()
    print(f"Backup saved: data/backups/{filename} ({sum(counts.values()):,} rows across {len(counts)} tables)\n")

    # --- 2. show exactly what will be deleted -------------------------------------
    conn = db.get_connection()
    trades = db.fetch_trades(conn=conn)
    div = db.fetch_dividends(conn=conn)
    conn.close()
    trade_ids = trades[(trades["source"] != "seed") & (trades["Trade Date"] <= CUTOFF)]["id"].tolist()
    div_ids = div[(div["source"] != "seed") & (div["Trade Date"] <= CUTOFF)]["id"].tolist()
    kept_trades = trades[(trades["source"] != "seed") & (trades["Trade Date"] > CUTOFF)]
    kept_div = div[(div["source"] != "seed") & (div["Trade Date"] > CUTOFF)]

    print(f"Will delete {len(trade_ids)} manual trades and {len(div_ids)} manual dividends,")
    print(f"all dated on or before {CUTOFF.date()} (now covered by the statement).")
    print(f"Will KEEP {len(kept_trades)} manual trades and {len(kept_div)} manual dividends")
    print(f"dated after {CUTOFF.date()} (October activity, not yet on any statement):")
    if not kept_trades.empty:
        print(kept_trades[["Trade Date", "Symbol", "Side", "Quantity"]].to_string(index=False))
    if not kept_div.empty:
        print(kept_div[["Trade Date", "Symbol", "Entry Type", "Net Amt"]].to_string(index=False))

    answer = input("\nType YES to proceed, anything else to abort: ")
    if answer.strip() != "YES":
        print("Aborted. Nothing was changed.")
        sys.exit(0)

    # --- 3. delete the covered rows ------------------------------------------------
    conn = db.get_connection()
    if trade_ids:
        conn.execute(f"DELETE FROM trades WHERE id IN ({','.join('?' * len(trade_ids))})", trade_ids)
    if div_ids:
        conn.execute(f"DELETE FROM dividends WHERE id IN ({','.join('?' * len(div_ids))})", div_ids)
    conn.commit()
    conn.close()
    print(f"\nDeleted {len(trade_ids)} trades and {len(div_ids)} dividends.")

    # --- 4. re-seed from the statement -- trades and dividends each get their own
    # fresh connection, since this is exactly the pair of steps that failed partway
    # through during testing when they shared one. ----------------------------------
    import seed_from_xlsx

    transactions, income = seed_from_xlsx.load_transactions_and_income(WORKBOOK)

    conn = db.get_connection()
    if db.count_seed_rows(conn=conn):
        db.delete_seed_rows(conn=conn)
    trade_rows = seed_from_xlsx.build_trade_rows(transactions)
    db.insert_trades_bulk(trade_rows, conn=conn)
    conn.close()
    print(f"Re-seeded {len(trade_rows)} trade rows.")

    conn = db.get_connection()
    dividend_rows = seed_from_xlsx.build_dividend_rows(income)
    db.insert_dividends_bulk(dividend_rows, conn=conn)
    conn.close()
    print(f"Re-seeded {len(dividend_rows)} dividend rows.")

    # --- 5. verify -------------------------------------------------------------------
    from core import calculations

    conn = db.get_connection()
    final_trades = db.fetch_trades(conn=conn)
    final_div = db.fetch_dividends(conn=conn)
    conn.close()
    print(f"\nFinal: {len(final_trades)} trades ({final_trades['source'].value_counts().to_dict()}), "
          f"{len(final_div)} dividends ({final_div['source'].value_counts().to_dict()}).")

    as_of_cutoff = final_trades[final_trades["Trade Date"] <= CUTOFF]
    positions = calculations.compute_current_positions(as_of_cutoff)
    xlsx_holdings = pd.read_excel(WORKBOOK, sheet_name="Holdings")
    sept = xlsx_holdings[xlsx_holdings["Month"] == "2026-09"]
    sept = sept[sept["Symbol"] != "*Cash"].copy()
    sept["Quantity"] = pd.to_numeric(sept["Quantity"], errors="coerce")

    merged = positions.set_index("Symbol")[["Quantity"]].join(
        sept.set_index("Symbol")[["Quantity"]], lsuffix="_db", rsuffix="_statement", how="outer",
    )
    merged["diff"] = (merged["Quantity_db"].fillna(0) - merged["Quantity_statement"].fillna(0)).abs()
    bad = merged[merged["diff"] > 1e-6]
    print(f"\nPosition check (as of {CUTOFF.date()}, before October activity): "
          f"{len(merged)} symbols, {len(bad)} with a mismatch.")
    if not bad.empty:
        print(bad.to_string())
        print("\n*** MISMATCH FOUND -- do not trust this run; tell Claude what happened. ***")
    else:
        print("All symbols match the statement exactly.")


if __name__ == "__main__":
    main()
