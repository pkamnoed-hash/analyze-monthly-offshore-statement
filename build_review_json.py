import json
import sys
import pandas as pd

out = sys.argv[1]
months = sys.argv[2:]
categories = ["Summary", "Holdings", "Income", "Fees", "Transactions", "Deposits_and_Withdrawals"]
labels = {"Deposits_and_Withdrawals": "Deposits & Withdrawals"}

xls = pd.ExcelFile(r"c:\Users\ADMIN\OneDrive\Desktop\Claude\VS Code\analyze monthly offshore statement\data\Offshore_Statements_2023-01_to_2026-04.xlsx")
existing_summary = pd.read_excel(xls, "Summary")
last_ending = existing_summary.iloc[-1]["Ending Cash ($)"]

result = {"months": months, "categories": {}, "reconciliation": []}

for cat in categories:
    dfs = [pd.read_csv(f"{out}/{m}_{cat}.csv") for m in months]
    combined = pd.concat(dfs, ignore_index=True)
    cols = combined.columns.tolist()
    rows = json.loads(combined.to_json(orient="records"))
    result["categories"][labels.get(cat, cat)] = {"columns": cols, "rows": rows, "count": len(rows)}

for month in months:
    summary = pd.read_csv(f"{out}/{month}_Summary.csv").iloc[0]
    holdings = pd.read_csv(f"{out}/{month}_Holdings.csv")
    income = pd.read_csv(f"{out}/{month}_Income.csv")
    txns = pd.read_csv(f"{out}/{month}_Transactions.csv")

    cash_identity = round(summary["Beginning Balance ($)"] + summary["Addition ($)"] - summary["Subtraction ($)"]
                           + summary["Trade Transaction ($)"] + summary["Cost and Fees ($)"] - summary["Ending Cash ($)"], 2)
    txn_diff = round(txns["Amount"].sum() - summary["Trade Transaction ($)"], 2)
    div_diff = round(income[income["Entry Type"] == "Dividends"]["Net Amt"].sum() - summary["Dividend ($)"], 2)
    holdings_diff = round(holdings[holdings["Symbol"] != "*Cash"]["Market Value"].sum() - summary["Long ($)"], 2)
    prior_diff = round(last_ending - summary["Beginning Balance ($)"], 2)

    result["reconciliation"].append({
        "month": month,
        "checks": [
            {"label": "Cash Identity", "diff": cash_identity, "detail": "Beginning + Addition - Subtraction + Trade Txn + Fees - Ending"},
            {"label": "Transactions Sum vs Trade Transaction", "diff": txn_diff, "detail": "sum(Transactions.Amount) vs Summary"},
            {"label": "Gross Dividends Sum vs Summary", "diff": div_diff, "detail": "sum(Income Dividends) vs Summary Dividend"},
            {"label": "Holdings Sum vs Long", "diff": holdings_diff, "detail": "sum(Holdings.Market Value, ex-Cash) vs Summary Long"},
            {"label": "Prior Ending vs Beginning", "diff": prior_diff, "detail": "previous month Ending Cash vs this Beginning Balance"},
        ]
    })
    last_ending = summary["Ending Cash ($)"]

with open(f"{out}/review.json", "w") as f:
    json.dump(result, f)
print("wrote", f"{out}/review.json")
