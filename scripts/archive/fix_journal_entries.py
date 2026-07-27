"""Reclassify the 39 'Journal Entry(Cash)' rows sitting in the Income sheet
(Jan 2023 - Sep 2024) into the Deposits & Withdrawals sheet, matching how every
month from Nov 2024 onward already records the identical STKSWI-transfer-coded
cash movements. Both groups are the same kind of transaction; only their sheet
placement differed, which silently excluded the early ones from "Net Deposits"
and from Investment Gain/Loss alike.
"""

import pandas as pd

PATH = r"c:\Users\ADMIN\OneDrive\Desktop\Claude\VS Code\analyze monthly offshore statement\data\Offshore_Statements_2023-01_to_2026-06.xlsx"
SHEET_ORDER = ["Summary", "Holdings", "Transactions", "Income", "Fees", "Deposits & Withdrawals", "Validation"]

sheets = {name: pd.read_excel(PATH, sheet_name=name) for name in SHEET_ORDER}

income = sheets["Income"]
flows = sheets["Deposits & Withdrawals"]

is_journal = income["Entry Type"] == "Journal Entry(Cash)"
moved = income[is_journal].copy()
assert moved["Symbol"].isna().all(), "expected Symbol to be blank on these rows"

moved = moved.drop(columns=["Symbol"])
moved["Account No"] = 947159514
moved = moved[["Month", "Trade Date", "Entry Type", "Description", "Net Amt", "Account No"]]

sheets["Income"] = income[~is_journal].reset_index(drop=True)
sheets["Deposits & Withdrawals"] = (
    pd.concat([flows, moved], ignore_index=True)
    .sort_values(["Month", "Trade Date"], kind="stable")
    .reset_index(drop=True)
)

print(f"Moved {len(moved)} rows (${moved['Net Amt'].sum():,.2f}) from Income -> Deposits & Withdrawals")
print(f"Income: {len(income)} -> {len(sheets['Income'])} rows")
print(f"Deposits & Withdrawals: {len(flows)} -> {len(sheets['Deposits & Withdrawals'])} rows")

with pd.ExcelWriter(PATH, engine="openpyxl") as writer:
    for name in SHEET_ORDER:
        sheets[name].to_excel(writer, sheet_name=name, index=False)

print("Wrote", PATH)
