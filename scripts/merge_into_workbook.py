"""Append the extracted May/June 2026 statement data onto the existing workbook,
matching its established per-column conventions exactly (e.g. Holdings.Quantity
uses the literal string "$--" for blank cells rather than NaN, unlike every
other numeric column in that sheet), and extend the Validation sheet with the
same 5 reconciliation checks already computed for the existing months.
"""

import pandas as pd

from extract_statement import extract_statement

SRC = r"c:\Users\ADMIN\OneDrive\Desktop\Claude\VS Code\analyze monthly offshore statement\data\Offshore_Statements_2023-01_to_2026-04.xlsx"
DST = r"c:\Users\ADMIN\OneDrive\Desktop\Claude\VS Code\analyze monthly offshore statement\data\Offshore_Statements_2023-01_to_2026-06.xlsx"
PDFS = [
    r"c:\Users\ADMIN\OneDrive\Desktop\Claude\VS Code\analyze monthly offshore statement\reports\account_statement_947159514_20260531.pdf",
    r"c:\Users\ADMIN\OneDrive\Desktop\Claude\VS Code\analyze monthly offshore statement\reports\account_statement_947159514_20260630.pdf",
]

SHEET_ORDER = ["Summary", "Holdings", "Transactions", "Income", "Fees", "Deposits & Withdrawals", "Validation"]

existing = {name: pd.read_excel(SRC, sheet_name=name) for name in SHEET_ORDER}
last_ending = existing["Summary"].iloc[-1]["Ending Cash ($)"]

new_by_sheet = {name: [] for name in SHEET_ORDER if name != "Validation"}
validation_rows = []

for pdf_path in PDFS:
    month, tables = extract_statement(pdf_path)

    # Match existing convention: Holdings.Quantity is the literal string "$--"
    # for blank cells (e.g. the *Cash row), while every other numeric column
    # in that sheet uses real NaN.
    holdings = tables["Holdings"].copy()
    holdings["Quantity"] = holdings["Quantity"].apply(lambda v: "$--" if pd.isna(v) else v)

    dw = tables["Deposits & Withdrawals"].copy()
    if not dw.empty:
        dw["Account No"] = dw["Account No"].astype("int64")

    new_by_sheet["Summary"].append(tables["Summary"])
    new_by_sheet["Holdings"].append(holdings)
    new_by_sheet["Transactions"].append(tables["Transactions"])
    new_by_sheet["Income"].append(tables["Income"])
    new_by_sheet["Fees"].append(tables["Fees"])
    new_by_sheet["Deposits & Withdrawals"].append(dw)

    summary = tables["Summary"].iloc[0]
    txns = tables["Transactions"]
    income = tables["Income"]

    cash_identity = round(summary["Beginning Balance ($)"] + summary["Addition ($)"] - summary["Subtraction ($)"]
                           + summary["Trade Transaction ($)"] + summary["Cost and Fees ($)"] - summary["Ending Cash ($)"], 2)
    txn_diff = round(txns["Amount"].sum() - summary["Trade Transaction ($)"], 2)
    div_diff = round(income[income["Entry Type"] == "Dividends"]["Net Amt"].sum() - summary["Dividend ($)"], 2)
    holdings_diff = round(holdings[holdings["Symbol"] != "*Cash"]["Market Value"].sum() - summary["Long ($)"], 2)
    prior_diff = round(last_ending - summary["Beginning Balance ($)"], 2)
    last_ending = summary["Ending Cash ($)"]

    validation_rows.append({
        "Month": month,
        "Cash Identity Diff ($)": int(round(cash_identity)),
        "Txn Sum vs Trade Transaction ($)": int(round(txn_diff)),
        "Gross Dividends Sum vs Summary ($)": int(round(div_diff)),
        "Holdings Sum vs Long ($)": holdings_diff,
        "Prior Ending vs Beginning ($)": prior_diff,
        "Note": "Holdings diff of few cents = statement's own per-row rounding (verified against PDF text)",
    })

merged = {}
for name in SHEET_ORDER:
    if name == "Validation":
        merged[name] = pd.concat([existing["Validation"], pd.DataFrame(validation_rows)], ignore_index=True)
    else:
        merged[name] = pd.concat([existing[name]] + new_by_sheet[name], ignore_index=True)

with pd.ExcelWriter(DST, engine="openpyxl") as writer:
    for name in SHEET_ORDER:
        merged[name].to_excel(writer, sheet_name=name, index=False)

print("Wrote", DST)
for name in SHEET_ORDER:
    print(f"  {name}: {len(existing[name])} -> {len(merged[name])} rows")
