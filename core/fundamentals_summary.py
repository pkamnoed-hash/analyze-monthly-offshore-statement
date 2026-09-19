"""V4.15 -- plain-text summary of one symbol's stored fundamentals, for the Hermes MCP
server's get_company_fundamentals tool (mcp_server/portfolio_mcp.py).

Says what app_pages/company_fundamentals.py shows -- Company Profile, Analyst Target
valuation, latest-year KPIs with year-over-year change, key ratios -- as text a chat
bot can relay. Every number comes from the same shared functions the page calls
(core/calculations.py: valuation_assessment, statement_series, yoy_pct,
compute_key_ratios), so the bot and the page can't disagree. Pure and Streamlit/DB-free
by design, like the rest of core/, so it's unit testable (tests/test_fundamentals_summary.py)
without the MCP package or a database.

V4.16 adds the Summary of Health (core/health.py) to that answer, and
summarize_holdings_health(): the same verdict for every current holding at once, for the
get_holdings_health tool.
"""

import re

import pandas as pd

from core import calculations, health

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
    result = health.assess_health(
        statements["income"], statements["balance"], statements["cashflow"], row.get("Sector"), row.get("Industry")
    )
    if result is not None:
        lines.append(health.describe_health(result))
    return "\n".join(lines)


def summarize_holdings_health(symbols, cache: pd.DataFrame) -> str:
    """V4.16 -- the health verdict of every current holding, for get_holdings_health.

    `symbols` are the currently-held symbols; `cache` is db.fetch_fundamentals_cache() (may
    be empty). Read-only over what is already stored -- nothing is fetched. Groups the
    holdings Weak / Mixed / Healthy with the reasons for each Weak and Mixed one (the same
    text the pages show), lists any that couldn't be rated, and names held symbols that
    have no stored statements: funds (real and common) and symbols never looked up. The
    date range of the stored data is stated because statements can be weeks old."""
    symbols = sorted(set(symbols))
    if not symbols:
        return "No current holdings."

    stored = {} if cache is None or cache.empty else {r["Symbol"]: r for _, r in cache.iterrows()}
    by_light = {health.RED: [], health.YELLOW: [], health.GREEN: []}
    not_rated, no_statements, not_stored, fetched = [], [], [], []
    for symbol in symbols:
        row = stored.get(symbol)
        if row is None:
            not_stored.append(symbol)
            continue
        result = health.assess_health(
            _as_dict(row.get("Income Statement")), _as_dict(row.get("Balance Sheet")), _as_dict(row.get("Cash Flow")),
            row.get("Sector"), row.get("Industry"),
        )
        if result is None:
            no_statements.append(symbol)
            continue
        if _present(row.get("Fetched At")):
            fetched.append(pd.Timestamp(row["Fetched At"]))
        if result["overall"] is None:
            not_rated.append(symbol)
        else:
            by_light[result["overall"]].append((symbol, result))

    def tag(symbol, result):
        return symbol + (" (partial)" if result["partial"] else "")

    with_statements = sum(len(v) for v in by_light.values()) + len(not_rated)
    lines = [
        f"Health of your {len(symbols)} current holdings -- a rule-of-thumb read of each company's stored annual "
        "statements, not investment advice."
    ]
    if with_statements:
        dates = f" (stored {min(fetched):%d/%m/%Y} to {max(fetched):%d/%m/%Y})" if fetched else ""
        lines.append(
            f"{with_statements} have statements{dates}; rated on profit & cash flow, debt & risk and growth "
            "against sector-aware thresholds."
        )
    for light in (health.RED, health.YELLOW):
        entries = by_light[light]
        if not entries:
            lines.append(f"{health.LABELS[light]}: none.")
            continue
        lines.append(f"{health.LABELS[light]} ({len(entries)}):")
        for symbol, result in entries:
            reasons = health.health_reasons(result)
            lines.append(f"- {tag(symbol, result)}: "
                         + ("; ".join(reasons) if reasons else "no single red measure, several middling readings"))
    healthy = by_light[health.GREEN]
    lines.append(
        f"{health.LABELS[health.GREEN]} ({len(healthy)}): " + (", ".join(tag(s, r) for s, r in healthy) or "none") + "."
    )
    watch = [(s, health.health_reasons(r)) for s, r in healthy if health.health_reasons(r)]
    if watch:
        lines.append("Healthy but with a red measure: " + "; ".join(f"{s} ({'; '.join(rs)})" for s, rs in watch) + ".")
    if any(r["partial"] for entries in by_light.values() for _, r in entries):
        lines.append("(partial) = bank/lender/fund-shaped statements, rated on return on equity and growth only.")
    if not_rated:
        lines.append(f"Not rated, too little data ({len(not_rated)}): {', '.join(not_rated)}.")
    if no_statements:
        lines.append(
            f"No financial statements, so not rated -- ETFs/funds ({len(no_statements)}): {', '.join(no_statements)}."
        )
    if not_stored:
        lines.append(
            f"Not stored yet, so not rated ({len(not_stored)}): {', '.join(not_stored)} "
            "(get_company_fundamentals looks a symbol up and saves it)."
        )
    return "\n".join(lines)
