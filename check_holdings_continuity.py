"""Per-symbol share-count audit: does each month's Holdings.Quantity match the
running total implied by that symbol's Transactions history (buys/sells,
Stock Splits, ReOrg CAs)? This is finer-grained than the workbook's existing
"Holdings Sum vs Long" check, which only validates an aggregate dollar total
and could mask a wrong quantity on one symbol netting against an error
elsewhere.

Mirrors the exact state-machine semantics in calculations.py's
compute_realized_pl (buy/sell are additive; a Stock Split's ADD row *replaces*
the running quantity rather than adding to it; ReOrg CA zeroes the position)
so this check is consistent with how the dashboard itself interprets these
entry types.
"""

import pandas as pd

WORKBOOK = r"c:\Users\ADMIN\OneDrive\Desktop\Claude\VS Code\analyze monthly offshore statement\data\Offshore_Statements_2023-01_to_2026-06.xlsx"

xls = pd.ExcelFile(WORKBOOK)
transactions = pd.read_excel(xls, "Transactions")
holdings = pd.read_excel(xls, "Holdings")
transactions["Trade Date"] = pd.to_datetime(transactions["Trade Date"], format="%m/%d/%Y", errors="coerce")

all_months = sorted(set(transactions["Month"]) | set(holdings["Month"]))

holdings_qty = {}
for _, row in holdings.iterrows():
    if row["Symbol"] == "*Cash":
        continue
    q = row["Quantity"]
    if isinstance(q, str):  # the "$--" placeholder, shouldn't occur for real symbols
        continue
    holdings_qty[(row["Symbol"], row["Month"])] = q

transactions["_entry_order"] = (transactions["Entry Type"] == "Trade Entry").astype(int)
tx = transactions[transactions["Symbol"].notna()].sort_values(["Symbol", "Trade Date", "_entry_order"])

mismatches = []
ghost_holdings = []  # symbol appears in Holdings for a month with no transaction history reaching it
checked_pairs = 0

for symbol, grp in tx.groupby("Symbol"):
    running_qty = 0.0
    grp_months = grp["Month"].tolist()
    month_idx = 0
    # walk every month from this symbol's first transaction month to the last month in the workbook,
    # applying that month's transactions (if any) then comparing to stored Holdings.Quantity
    first_month = grp_months[0]
    for month in all_months:
        if month < first_month:
            continue
        month_rows = grp[grp["Month"] == month]
        for _, r in month_rows.iterrows():
            entry_type = r["Entry Type"]
            side = r["Side"]
            quantity = r["Quantity"] if pd.notna(r["Quantity"]) else 0.0
            if entry_type == "Trade Entry":
                running_qty += quantity  # sells are already stored negative
            elif entry_type == "Stock Split":
                if quantity < 0:
                    pass  # REMOVE row: no-op, paired ADD row sets the new absolute qty
                else:
                    running_qty = quantity
            elif entry_type == "ReOrg CA":
                running_qty = 0.0
            elif quantity:
                running_qty += quantity  # e.g. a rights-offering distribution (blank Entry Type)

        stored = holdings_qty.get((symbol, month))
        checked_pairs += 1
        if stored is None:
            if abs(running_qty) > 1e-4:
                mismatches.append((symbol, month, "missing_from_holdings", running_qty, None))
        else:
            if abs(running_qty - stored) > 1e-4:
                mismatches.append((symbol, month, "quantity_mismatch", running_qty, stored))

    # after the last month this symbol has transactions, confirm it doesn't linger in Holdings
    # with a nonzero quantity in a later month with no further transaction activity
    if abs(running_qty) < 1e-4:
        later_months = [m for m in all_months if m > grp_months[-1]]
        for m in later_months:
            stored = holdings_qty.get((symbol, m))
            if stored is not None and abs(stored) > 1e-4:
                ghost_holdings.append((symbol, m, stored))

print(f"Symbols checked: {tx['Symbol'].nunique()}")
print(f"Symbol-month pairs checked: {checked_pairs}")
print(f"Quantity mismatches: {len(mismatches)}")
for m in mismatches:
    print("  ", m)
print(f"Ghost holdings (nonzero qty lingering after transaction history implies exit): {len(ghost_holdings)}")
for g in ghost_holdings:
    print("  ", g)

# separately: any symbol in Holdings with NO transaction history at all (position that predates
# any recorded buy -- would indicate a real gap, since we already confirmed Jan 2023 is inception)
holdings_symbols = set(s for s, m in holdings_qty.keys())
tx_symbols = set(tx["Symbol"].unique())
orphan_symbols = holdings_symbols - tx_symbols
print(f"\nSymbols in Holdings with zero matching Transactions ever: {sorted(orphan_symbols)}")
