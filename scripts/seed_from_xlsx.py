"""One-time import of the official xlsx Transactions/Income history into
SQLite as baseline data (source='seed'), so FIFO lot-matching and dividend
continuity work from day one for trades/dividends logged after this point.

Usage:
    python scripts/seed_from_xlsx.py            # skip if already seeded
    python scripts/seed_from_xlsx.py --force    # wipe existing seed rows, re-seed
"""

import os
import sys

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import db  # noqa: E402  (needs sys.path set up above)

XLSX_PATH = os.path.join(ROOT, "data", "Offshore_Statements_2023-01_to_2026-06.xlsx")

DIVIDEND_ENTRY_TYPES = ["Dividends", "Div. Adj(NRA Withheld)"]


def load_transactions_and_income(path):
    xls = pd.ExcelFile(path)
    transactions = pd.read_excel(xls, "Transactions")
    income = pd.read_excel(xls, "Income")
    transactions["Trade Date"] = pd.to_datetime(transactions["Trade Date"], format="%m/%d/%Y", errors="coerce")
    income["Trade Date"] = pd.to_datetime(income["Trade Date"], format="%m/%d/%Y", errors="coerce")
    return transactions, income


def build_trade_rows(transactions):
    """Filtered to Symbol.notna(), matching compute_realized_pl's own filter --
    journal/cash rows in Transactions don't belong in a stock-trade table."""
    tx = transactions[transactions["Symbol"].notna()].copy()
    rows = []
    for _, r in tx.iterrows():
        rows.append({
            "trade_date": r["Trade Date"].strftime("%Y-%m-%d") if pd.notna(r["Trade Date"]) else None,
            # A blank Entry Type is a real case in this data (e.g. a rights-offering
            # distribution -- see calculations.py's fallback branch); store "" rather
            # than None since the column is NOT NULL and "" preserves "blank" fidelity.
            "entry_type": r["Entry Type"] if pd.notna(r["Entry Type"]) else "",
            "side": r["Side"].lower() if pd.notna(r["Side"]) else None,
            "symbol": r["Symbol"],
            "description": r["Description"] if pd.notna(r["Description"]) else None,
            "quantity": float(r["Quantity"]) if pd.notna(r["Quantity"]) else 0.0,
            "price": float(r["Price"]) if pd.notna(r["Price"]) else None,
            "amount": float(r["Amount"]) if pd.notna(r["Amount"]) else None,
            "commission": float(r["Commission"]) if pd.notna(r["Commission"]) else None,
            "source": "seed",
            "notes": "seeded from Offshore_Statements xlsx",
        })
    return rows


def build_dividend_rows(income):
    rows = []

    div = income[income["Entry Type"].isin(DIVIDEND_ENTRY_TYPES) & income["Symbol"].notna()].copy()
    grouped = div.groupby(["Trade Date", "Symbol"], as_index=False)["Net Amt"].sum()
    for _, r in grouped.iterrows():
        rows.append({
            "trade_date": r["Trade Date"].strftime("%Y-%m-%d"),
            "symbol": r["Symbol"],
            "entry_type": "Dividend",
            "net_amount": float(r["Net Amt"]),
            "source": "seed",
            "notes": "seeded from Offshore_Statements xlsx (net of NRA withholding)",
        })

    interest = income[income["Entry Type"] == "Credit/Margin Interest"].copy()
    for _, r in interest.iterrows():
        if pd.isna(r["Trade Date"]) or pd.isna(r["Net Amt"]):
            continue
        rows.append({
            "trade_date": r["Trade Date"].strftime("%Y-%m-%d"),
            "symbol": None,
            "entry_type": "Interest",
            "net_amount": float(r["Net Amt"]),
            "source": "seed",
            "notes": "seeded from Offshore_Statements xlsx",
        })

    return rows


def main():
    force = "--force" in sys.argv

    conn = db.get_connection()
    db.init_db(conn=conn)

    existing = db.count_seed_rows(conn=conn)
    if existing and not force:
        print(f"Already seeded ({existing} seed trade rows present). Pass --force to re-seed.")
        conn.close()
        return
    if existing and force:
        print(f"--force: deleting {existing} existing seed rows before re-seeding...")
        db.delete_seed_rows(conn=conn)

    transactions, income = load_transactions_and_income(XLSX_PATH)

    trade_rows = build_trade_rows(transactions)
    db.insert_trades_bulk(trade_rows, conn=conn)

    dividend_rows = build_dividend_rows(income)
    db.insert_dividends_bulk(dividend_rows, conn=conn)

    conn.close()

    xlsx_tx_count = int(transactions["Symbol"].notna().sum())
    xlsx_div_rows = income[income["Entry Type"].isin(DIVIDEND_ENTRY_TYPES) & income["Symbol"].notna()]
    xlsx_interest_count = int((income["Entry Type"] == "Credit/Margin Interest").sum())

    print(f"Seeded {len(trade_rows)} trade rows (xlsx had {xlsx_tx_count} Transactions rows with a Symbol).")
    print(
        f"Seeded {len(dividend_rows)} dividend rows "
        f"({len(xlsx_div_rows)} xlsx dividend Income rows grouped into {grouped_count(xlsx_div_rows)} by date+symbol, "
        f"plus {xlsx_interest_count} interest rows)."
    )


def grouped_count(div_rows):
    return div_rows.groupby(["Trade Date", "Symbol"]).ngroups if not div_rows.empty else 0


if __name__ == "__main__":
    main()
