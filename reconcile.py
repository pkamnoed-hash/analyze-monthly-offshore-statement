"""Reconcile extracted PDF tables against the statement's own printed control
totals, using the same 5 checks as the workbook's existing Validation sheet."""

import sys
import pandas as pd

out = sys.argv[1]  # e.g. .../extracted
months = sys.argv[2:]  # e.g. 2026-05 2026-06

xls = pd.ExcelFile(r"c:\Users\ADMIN\OneDrive\Desktop\Claude\VS Code\analyze monthly offshore statement\data\Offshore_Statements_2023-01_to_2026-04.xlsx")
existing_summary = pd.read_excel(xls, "Summary")
last_ending = existing_summary.iloc[-1]["Ending Cash ($)"]  # April 2026, chains into May

for month in months:  # must be passed in chronological order
    summary = pd.read_csv(f"{out}/{month}_Summary.csv").iloc[0]
    holdings = pd.read_csv(f"{out}/{month}_Holdings.csv")
    income = pd.read_csv(f"{out}/{month}_Income.csv")
    txns = pd.read_csv(f"{out}/{month}_Transactions.csv")

    cash_identity = (summary["Beginning Balance ($)"] + summary["Addition ($)"] - summary["Subtraction ($)"]
                      + summary["Trade Transaction ($)"] + summary["Cost and Fees ($)"] - summary["Ending Cash ($)"])
    txn_sum_diff = round(txns["Amount"].sum() - summary["Trade Transaction ($)"], 2)
    gross_div_diff = round(income[income["Entry Type"] == "Dividends"]["Net Amt"].sum() - summary["Dividend ($)"], 2)
    holdings_sum_diff = round(holdings[holdings["Symbol"] != "*Cash"]["Market Value"].sum() - summary["Long ($)"], 2)
    prior_diff = round(last_ending - summary["Beginning Balance ($)"], 2)

    print(f"=== {month} ===")
    print(f"  Cash Identity Diff ($):              {round(cash_identity, 2)}")
    print(f"  Txn Sum vs Trade Transaction ($):     {txn_sum_diff}")
    print(f"  Gross Dividends Sum vs Summary ($):   {gross_div_diff}")
    print(f"  Holdings Sum vs Long ($):             {holdings_sum_diff}")
    print(f"  Prior Ending vs Beginning ($):        {prior_diff}")

    last_ending = summary["Ending Cash ($)"]
