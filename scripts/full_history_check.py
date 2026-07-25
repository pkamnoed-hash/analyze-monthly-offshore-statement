"""Independently re-extract all 42 monthly statement PDFs and:
  1) recompute the same 5 reconciliation checks as the workbook's Validation
     sheet, from freshly-extracted data, against each statement's own printed
     control totals (validates extraction is internally self-consistent); and
  2) diff the freshly-extracted Summary row against what's actually stored in
     the workbook for that month (validates the "database" matches source).
"""

import glob
import json

import pandas as pd

from extract_statement import extract_statement

WORKBOOK = r"c:\Users\ADMIN\OneDrive\Desktop\Claude\VS Code\analyze monthly offshore statement\data\Offshore_Statements_2023-01_to_2026-06.xlsx"

pdf_paths = [
    p for p in glob.glob(
        r"c:\Users\ADMIN\OneDrive\Desktop\Claude\VS Code\analyze monthly offshore statement\reports\**\*.pdf",
        recursive=True,
    )
    if "former request" not in p
]
print(f"Found {len(pdf_paths)} statement PDFs to process")

stored_summary = pd.read_excel(WORKBOOK, sheet_name="Summary").set_index("Month")
SUMMARY_COLS = [c for c in stored_summary.columns if c not in ("Statement Period", "Account No")]

results = []
for path in pdf_paths:
    try:
        month, tables = extract_statement(path)
    except Exception as e:
        results.append({"month": None, "path": path, "error": str(e)})
        continue

    summary = tables["Summary"].iloc[0]
    txns = tables["Transactions"]
    income = tables["Income"]
    holdings = tables["Holdings"]

    def z(v):  # first-ever month has a blank Beginning Balance ("$ --"); treat as 0 for the identity check only
        return 0.0 if v is None or (isinstance(v, float) and pd.isna(v)) else v

    cash_identity = round(z(summary["Beginning Balance ($)"]) + z(summary["Addition ($)"]) - z(summary["Subtraction ($)"])
                           + z(summary["Trade Transaction ($)"]) + z(summary["Cost and Fees ($)"]) - z(summary["Ending Cash ($)"]), 2)
    txn_diff = round(txns["Amount"].sum() - z(summary["Trade Transaction ($)"]), 2)
    div_diff = round(income[income["Entry Type"] == "Dividends"]["Net Amt"].sum() - z(summary["Dividend ($)"]), 2)
    holdings_diff = round(holdings[holdings["Symbol"] != "*Cash"]["Market Value"].sum() - z(summary["Long ($)"]), 2)

    stored_diffs = {}
    if month in stored_summary.index:
        stored_row = stored_summary.loc[month]
        for col in SUMMARY_COLS:
            fresh_val = summary[col]
            stored_val = stored_row[col]
            fresh_nan = pd.isna(fresh_val)
            stored_nan = pd.isna(stored_val)
            if fresh_nan and stored_nan:
                continue
            if fresh_nan != stored_nan or round(float(fresh_val) - float(stored_val), 2) != 0:
                stored_diffs[col] = {
                    "fresh": None if fresh_nan else round(float(fresh_val), 2),
                    "stored": None if stored_nan else round(float(stored_val), 2),
                }
    else:
        stored_diffs["__missing_month__"] = True

    results.append({
        "month": month,
        "path": path,
        "cash_identity": cash_identity,
        "txn_diff": txn_diff,
        "div_diff": div_diff,
        "holdings_diff": holdings_diff,
        "summary_diffs": stored_diffs,
    })

results.sort(key=lambda r: r.get("month") or "")

with open("full_history_check.json", "w") as f:
    json.dump(results, f, indent=2)

print(f"\n{len(results)} months processed. Extraction errors: {sum(1 for r in results if 'error' in r)}")

months_seen = {r["month"] for r in results if r.get("month")}
expected_months = pd.period_range("2023-01", "2026-06", freq="M").astype(str).tolist()
missing = sorted(set(expected_months) - months_seen)
extra = sorted(months_seen - set(expected_months))
print(f"Missing months (no PDF found): {missing}")
print(f"Unexpected/duplicate months (multiple PDFs same month?): {[m for m in months_seen if sum(1 for r in results if r.get('month')==m) > 1]}")

print("\n=== Months with any nonzero reconciliation diff beyond rounding, or Summary mismatch ===")
for r in results:
    if "error" in r:
        print(f"  {r['path']}: ERROR {r['error']}")
        continue
    issues = []
    if abs(r["cash_identity"]) > 0.02:
        issues.append(f"cash_identity={r['cash_identity']}")
    if abs(r["txn_diff"]) > 0.02:
        issues.append(f"txn_diff={r['txn_diff']}")
    if abs(r["div_diff"]) > 0.02:
        issues.append(f"div_diff={r['div_diff']}")
    if abs(r["holdings_diff"]) > 0.10:
        issues.append(f"holdings_diff={r['holdings_diff']}")
    if r["summary_diffs"]:
        issues.append(f"summary_diffs={r['summary_diffs']}")
    if issues:
        print(f"  {r['month']}: {issues}")
print("(done)")
