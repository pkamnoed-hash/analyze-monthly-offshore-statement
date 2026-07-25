"""Extract Summary/Holdings/Income/Fees/Transaction/Deposits data from an Alpaca
monthly statement PDF into DataFrames matching the existing workbook's schema.

Grid-based (pdfplumber find_tables/extract) rather than text-stream based, since
the Transaction table has no whitespace between fields in the raw text layer --
only the ruled-line grid reliably separates its columns.
"""

import re
import sys
import json

import pandas as pd
import pdfplumber

CATEGORY_HEADERS = {
    "Holdings": ["Symbol", "Description", "Quantity", "Market Price", "Market Value", "Cost Price", "Unrealized", "TD Cost Basis"],
    "Income": ["Trade Date", "Entry Type", "Symbol", "Description", "Net Amt"],
    "Fees": ["Trade Date", "Description", "Net Amt"],
    "Transaction": ["Trade Date", "Entry Type", "Side", "Symbol", "Description", "Quantity", "Price", "Amount", "Commission"],
    "Deposit & Withdrawals": ["Trade Date", "Entry Type", "Description", "Net Amt", "Account No"],
}

TARGET_COLUMNS = {
    "Holdings": ["Symbol", "Description", "Quantity", "Market Price", "Market Value", "Cost Price", "Unrealized", "TD Cost Basis"],
    "Income": ["Trade Date", "Entry Type", "Symbol", "Description", "Net Amt"],
    "Fees": ["Trade Date", "Description", "Net Amt"],
    "Transactions": ["Trade Date", "Entry Type", "Side", "Symbol", "Description", "Quantity", "Price", "Amount", "Commission"],
    "Deposits & Withdrawals": ["Trade Date", "Entry Type", "Description", "Net Amt", "Account No"],
}
NUMERIC_COLS = {
    "Holdings": {"Quantity", "Market Price", "Market Value", "Cost Price", "Unrealized", "TD Cost Basis"},
    "Income": {"Net Amt"},
    "Fees": {"Net Amt"},
    "Transactions": {"Quantity", "Price", "Amount", "Commission"},
    "Deposits & Withdrawals": {"Net Amt", "Account No"},
}
PDF_TO_SHEET_NAME = {
    "Holdings": "Holdings",
    "Income": "Income",
    "Fees": "Fees",
    "Transaction": "Transactions",
    "Deposit & Withdrawals": "Deposits & Withdrawals",
}


def clean_str(s):
    if s is None:
        return None
    s = str(s).strip()
    return s if s else None


def clean_num(s):
    s = clean_str(s)
    if s is None or s in ("$ --", "$--", "--"):
        return None
    neg = s.startswith("-")
    stripped = s.replace("$", "").replace(",", "").lstrip("-").strip()
    try:
        v = float(stripped)
    except ValueError:
        return None
    return -v if neg else v


def parse_month_period(full_text):
    m = re.search(r"Period:\s*([A-Z]+)\s*-\s*(\d{4})", full_text)
    if not m:
        raise ValueError("Could not find statement period in PDF text")
    month_name, year = m.group(1).title(), m.group(2)
    dt = pd.to_datetime(f"{month_name} {year}", format="%B %Y")
    return dt.strftime("%Y-%m"), f"{month_name} {year}"


def parse_account_no(full_text):
    m = re.search(r"Account No:\s*(\d+)", full_text)
    if not m:
        raise ValueError("Could not find account number in PDF text")
    return int(m.group(1))


def extract_realized_section(rows):
    """Rows from the 'Realized Gain/Loss from Sales' table. 'Gain'/'Loss'/'Net'
    each appear twice (once under Short Term, once under Long Term), so this
    must track which section we're in rather than key by label alone."""
    section = None
    result = {}
    for row in rows[1:]:
        label = clean_str(row[0])
        if label in ("Short Term", "Long Term"):
            section = label
            continue
        if label == "Net" and section:
            result[section] = clean_num(row[1])
    return result


def extract_summary(pdf, month, period, account_no):
    label_map = {}
    realized = {}
    for t in pdf.pages[0].find_tables():
        rows = t.extract()
        title = clean_str(rows[0][0])
        if title == "Realized Gain/Loss from Sales":
            realized = extract_realized_section(rows)
            continue
        for row in rows[1:]:
            label = clean_str(row[0])
            if label is None:
                continue
            label_map[label] = row[1] if len(row) > 1 else None

    return {
        "Month": month,
        "Statement Period": period,
        "Account No": account_no,
        "Beginning Balance ($)": clean_num(label_map.get("Beginning Balance")),
        "Addition ($)": clean_num(label_map.get("Addition")),
        "Subtraction ($)": clean_num(label_map.get("Subtraction")),
        "Trade Transaction ($)": clean_num(label_map.get("Trade Transaction")),
        "Cost and Fees ($)": clean_num(label_map.get("Cost and Fees")),
        "Ending Cash ($)": clean_num(label_map.get("Ending Value")),
        "Long ($)": clean_num(label_map.get("Long")),
        "Total Market Value ($)": clean_num(label_map.get("Total Market Value")),
        "Dividend ($)": clean_num(label_map.get("Dividend")),
        "Interest ($)": clean_num(label_map.get("Interest**")),
        "Realized ST Net ($)": realized.get("Short Term"),
        "Realized LT Net ($)": realized.get("Long Term"),
    }


def collect_category_rows(pdf):
    """Single sequential pass over every page/table/row in document order,
    tagging rows with whichever category title was most recently seen. This
    naturally scopes multi-page tables (title + header repeat on every page)
    without hardcoding page ranges, which differ between statements."""
    buckets = {k: [] for k in CATEGORY_HEADERS}
    current = None
    for page in pdf.pages:
        for t in page.find_tables():
            for row in t.extract():
                cleaned = [clean_str(c) for c in row]
                first = cleaned[0]
                rest_blank = all(c is None for c in cleaned[1:])

                if first in CATEGORY_HEADERS and rest_blank:
                    current = first
                    continue
                if current and cleaned == CATEGORY_HEADERS[current]:
                    continue
                if all(c is None for c in cleaned):
                    continue
                if first == "No record found.":
                    continue
                if current:
                    buckets[current].append(row)
    return buckets


def extract_statement(pdf_path):
    with pdfplumber.open(pdf_path) as pdf:
        full_text = "\n".join((p.extract_text() or "") for p in pdf.pages[:1])
        month, period = parse_month_period(full_text)
        account_no = parse_account_no(full_text)

        summary = extract_summary(pdf, month, period, account_no)
        buckets = collect_category_rows(pdf)

    tables = {"Summary": pd.DataFrame([summary])}
    for pdf_key, sheet_name in PDF_TO_SHEET_NAME.items():
        cols = TARGET_COLUMNS[sheet_name]
        rows = buckets[pdf_key]
        df = pd.DataFrame(rows, columns=cols)
        for col in cols:
            if col in NUMERIC_COLS[sheet_name]:
                df[col] = df[col].map(clean_num)
            else:
                df[col] = df[col].map(clean_str)
        df.insert(0, "Month", month)
        tables[sheet_name] = df
    return month, tables


if __name__ == "__main__":
    pdf_path = sys.argv[1]
    out_prefix = sys.argv[2]
    month, tables = extract_statement(pdf_path)
    for name, df in tables.items():
        df.to_csv(f"{out_prefix}_{name.replace(' ', '_').replace('&', 'and')}.csv", index=False)
    counts = {name: len(df) for name, df in tables.items()}
    print(json.dumps({"month": month, "row_counts": counts}, indent=2))
