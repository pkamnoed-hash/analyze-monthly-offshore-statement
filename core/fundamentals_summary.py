"""V4.15 -- plain-text summary of one symbol's stored fundamentals, for the Hermes MCP
server's get_company_fundamentals tool (mcp_server/portfolio_mcp.py).

Says what app_pages/company_fundamentals.py shows -- Company Profile, Analyst Target
valuation, latest-year KPIs with year-over-year change, key ratios -- as text a chat
bot can relay. Every number comes from the same shared functions the page calls
(core/calculations.py: valuation_assessment, statement_series, yoy_pct,
compute_key_ratios), so the bot and the page can't disagree. Pure and Streamlit/DB-free
by design, like the rest of core/, so it's unit testable (tests/test_fundamentals_summary.py)
without the MCP package or a database.
"""

import re

import pandas as pd

from core import calculations

_SUMMARY_CHARS = 300
_TICKER_RE = re.compile(r"^[A-Z0-9.^=\-]{1,20}$")

# (label shown, which statement, row label in that statement) -- the same four KPI
# cards the page shows.
_KPI_ROWS = [
    ("Revenue", "income", "Total Revenue"),
    ("Net income", "income", "Net Income"),
    ("Free cash flow", "cashflow", "Free Cash Flow"),
    ("Total debt", "balance", "Total Debt"),
]


def normalize_symbol(text) -> str | None:
    """Trimmed, upper-cased ticker if `text` looks like one (letters, digits and . ^ = -
    only, up to 20 characters -- covers BRK.B, BRK-B, ^GSPC, EURUSD=X), else None.
    Guards the MCP tool's input: a chat message becomes a database key there."""
    if not isinstance(text, str):
        return None
    candidate = text.strip().upper()
    return candidate if _TICKER_RE.match(candidate) else None


def _present(value) -> bool:
    return value is not None and pd.notna(value)


def _as_dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _truncate(text: str, limit: int) -> str:
    text = " ".join(str(text).split())
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0] + "..."


def _pct(value) -> str:
    return f"{value * 100:.1f}%" if value is not None else "N/A"


def _multiple(value) -> str:
    return f"{value:.2f}x" if value is not None else "N/A"


def summarize_fundamentals(row) -> str:
    """`row` is one row of db.fetch_fundamentals_cache() (a Series or dict keyed
    Symbol/Currency/Financial Currency/Current Price/Target Mean Price/Number Of
    Analysts/Income Statement/Balance Sheet/Cash Flow/Business Summary/Industry/
    Sector/Employees/Country/City/Fetched At). Missing values may be None or NaN --
    both are treated as absent, the same way the page does.

    An ETF/fund (all three statements empty, real and common: about half of a typical
    portfolio) gets the profile and valuation but no KPIs/ratios. Statement figures are
    labelled in the statement's own currency when it isn't USD -- unlike the page,
    which always prints "$" and relies on its mismatch banner -- because a chat answer
    has no banner and "$2,894,308M" for TSM (really TWD) would be badly misleading."""
    symbol = row["Symbol"]
    fetched = pd.Timestamp(row["Fetched At"]).strftime("%d/%m/%Y") if _present(row.get("Fetched At")) else "an unknown date"
    lines = [f"{symbol} -- stored data as of {fetched} (the price and statements below are from then)."]

    profile = []
    if _present(row.get("Industry")):
        profile.append(f"Industry: {row['Industry']}")
    if _present(row.get("Sector")):
        profile.append(f"Sector: {row['Sector']}")
    if _present(row.get("Employees")):
        profile.append(f"Employees: {int(row['Employees']):,}")
    market = ", ".join(str(v) for v in (row.get("City"), row.get("Country")) if _present(v))
    if market:
        profile.append(f"Market: {market}")
    if profile:
        lines.append("; ".join(profile))
    if _present(row.get("Business Summary")):
        lines.append("About: " + _truncate(row["Business Summary"], _SUMMARY_CHARS))

    price = row.get("Current Price")
    target = row.get("Target Mean Price")
    assessment = calculations.valuation_assessment(price, target)
    if assessment["verdict"] == "No coverage":
        price_text = f" Current price ${price:,.2f}." if _present(price) else ""
        lines.append("Valuation (analyst target): no analyst coverage." + price_text)
    else:
        pct, verdict = assessment["pct"], assessment["verdict"]
        if verdict == "Overvalued":
            relation = f"the price is {abs(pct):.1f}% above the target"
        elif verdict == "Undervalued":
            relation = f"the price is {abs(pct):.1f}% below the target"
        else:
            relation = f"the price is within 2% of the target ({pct:+.1f}%)"
        analysts = int(row["Number Of Analysts"]) if _present(row.get("Number Of Analysts")) else 0
        analyst_text = f" ({analysts} analyst{'s' if analysts != 1 else ''})" if analysts else ""
        lines.append(
            f"Valuation (analyst target): {verdict}. Current price ${price:,.2f} vs mean analyst "
            f"target ${target:,.2f}{analyst_text}; {relation}."
        )

    currency, financial_currency = row.get("Currency"), row.get("Financial Currency")
    if _present(currency) and _present(financial_currency) and currency != financial_currency:
        lines.append(
            f"Note: statements are reported in {financial_currency} but the price is quoted in {currency}, "
            "so the statement figures below aren't directly comparable to the price without a currency conversion."
        )

    statements = {
        "income": _as_dict(row.get("Income Statement")),
        "balance": _as_dict(row.get("Balance Sheet")),
        "cashflow": _as_dict(row.get("Cash Flow")),
    }
    if not any(statements.values()):
        lines.append(
            "No financial statements available for this symbol (likely an ETF or fund), "
            "so there are no revenue, profit or ratio figures."
        )
        return "\n".join(lines)

    unit = "$" if not _present(financial_currency) or financial_currency == "USD" else f"{financial_currency} "
    fiscal_year_end = None
    kpi_lines = []
    for label, source, row_label in _KPI_ROWS:
        dates, values = calculations.statement_series(statements[source], row_label)
        latest = values[-1] if values else None
        if _present(latest):
            text = f"{unit}{latest / 1e6:,.0f}M"
            yoy = calculations.yoy_pct(values)
            if yoy is not None:
                text += f" ({yoy:+.1f}% YoY)"
        else:
            text = "N/A"
        kpi_lines.append(f"- {label}: {text}")
        if row_label == "Total Revenue" and dates:
            fiscal_year_end = dates[-1]
    lines.append(f"Latest fiscal year{f' (ended {fiscal_year_end})' if fiscal_year_end else ''}:")
    lines.extend(kpi_lines)

    ratios = calculations.compute_key_ratios(statements["income"], statements["balance"])
    lines.append(
        f"Key ratios: gross margin {_pct(ratios['gross_margin'])}, operating margin {_pct(ratios['operating_margin'])}, "
        f"return on equity {_pct(ratios['return_on_equity'])}, debt/equity {_multiple(ratios['debt_to_equity'])}, "
        f"current ratio {_multiple(ratios['current_ratio'])}."
    )
    return "\n".join(lines)
