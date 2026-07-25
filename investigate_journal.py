import pandas as pd

pd.set_option("display.max_rows", None)
pd.set_option("display.width", 200)

xls = pd.ExcelFile("data/Offshore_Statements_2023-01_to_2026-06.xlsx")
income = pd.read_excel(xls, "Income")
flows = pd.read_excel(xls, "Deposits & Withdrawals")
summary = pd.read_excel(xls, "Summary")

print("=== Entry Types present in Income sheet ===")
print(income["Entry Type"].value_counts())
print()
print("=== Entry Types present in Deposits & Withdrawals sheet ===")
print(flows["Entry Type"].value_counts())
print()

je_income = income[income["Entry Type"] == "Journal Entry(Cash)"]
je_flows = flows[flows["Entry Type"] == "Journal Entry(Cash)"]

print(f"Journal Entry(Cash) rows in Income sheet: {len(je_income)}, sum = {je_income['Net Amt'].sum():,.2f}")
print(f"Journal Entry(Cash) rows in Deposits & Withdrawals sheet: {len(je_flows)}, sum = {je_flows['Net Amt'].sum():,.2f}")
print()

print("=== Journal Entry(Cash) in Income, by month ===")
print(je_income.groupby("Month")["Net Amt"].agg(["count", "sum"]))
print()
print("=== Journal Entry(Cash) in Deposits & Withdrawals, by month ===")
print(je_flows.groupby("Month")["Net Amt"].agg(["count", "sum"]))
print()

# Reproduce the dashboard's "All" range KPI math exactly
summary["Month"] = pd.to_datetime(summary["Month"], format="%Y-%m")
income["Month"] = pd.to_datetime(income["Month"], format="%Y-%m")
flows["Month"] = pd.to_datetime(flows["Month"], format="%Y-%m")

start, end = summary["Month"].min(), summary["Month"].max()
s = summary
inc = income
fl = flows

total_deposits = fl["Net Amt"].clip(lower=0).sum()
total_withdrawals = -fl["Net Amt"].clip(upper=0).sum()
net_flows = total_deposits - total_withdrawals
print(f"total_deposits (all D&W rows, any Entry Type, clipped>=0): {total_deposits:,.2f}")
print(f"total_withdrawals: {total_withdrawals:,.2f}")
print(f"net_flows (= 'Net Deposits (tracked)' KPI): {net_flows:,.2f}")
print()

# Is the Journal Entry(Cash) money in D&W already inside net_flows?
je_flows_in_range_sum = je_flows["Net Amt"].sum()
print(f"Of net_flows, {je_flows_in_range_sum:,.2f} comes from Journal Entry(Cash) rows sitting in D&W sheet")
print()

ending_value = s.iloc[-1]["Total Market Value ($)"]
start_value = 0.0  # no prior row before Jan 2023
balance_based_gain = ending_value - start_value - net_flows

dividend_types = ["Dividends", "Div. Adj(NRA Withheld)"]
div_in_range = inc[inc["Entry Type"].isin(dividend_types) & inc["Symbol"].notna()]
total_dividends = div_in_range["Net Amt"].sum()
total_interest = inc[inc["Entry Type"] == "Credit/Margin Interest"]["Net Amt"].sum()

print(f"ending_value: {ending_value:,.2f}")
print(f"balance_based_gain (ending - start - net_flows): {balance_based_gain:,.2f}")
print(f"total_dividends (net of NRA withholding): {total_dividends:,.2f}")
print(f"total_interest: {total_interest:,.2f}")
print(f"je_income sum (excluded from both realized/unrealized/div/interest AND from net_flows): {je_income['Net Amt'].sum():,.2f}")
