"""V4.16 -- rule-of-thumb "health" rating of one company from its stored annual statements.

Used by Company Fundamentals (Summary of Health), Monitor Stocks (Health column) and the
Hermes MCP server (get_company_fundamentals, get_holdings_health) so all three show the
same verdict. Pure and Streamlit/DB-free by design, like the rest of core/, so it's unit
testable (tests/test_health.py). Every input figure comes from the shared maths in
core/calculations.py (compute_key_ratios, statement_series, latest_statement_value,
safe_divide, yoy_pct).

Three groups of measures -- Profit & cash flow, Debt & risk, Growth -- each measure a
green/yellow/red light against a threshold pair. The thresholds depend on a sector
profile (a supermarket's margins aren't a software company's, a utility's debt isn't a
retailer's). A group's light is the mean of its measures; the overall light is the mean of
the group means, capped by the red-group and hard-flag rules in assess_health. These are
judgment-call rules of thumb on annual data, not advice -- the pages say so.
"""

import pandas as pd

from core import calculations

GREEN, YELLOW, RED = "green", "yellow", "red"
LABELS = {GREEN: "Healthy", YELLOW: "Mixed", RED: "Weak"}
DOTS = {GREEN: "🟢", YELLOW: "🟡", RED: "🔴"}
_SCORE = {GREEN: 2, YELLOW: 1, RED: 0}

PROFIT, DEBT, GROWTH = "Profit & cash flow", "Debt & risk", "Growth"
GROUPS = (PROFIT, DEBT, GROWTH)

FLAG_LOSS = "losing money"
FLAG_BURN = "burning cash"

# Threshold pairs are (green_at, yellow_at): at or beyond green_at is green, at or beyond
# yellow_at is yellow, anything worse is red. "Beyond" means >= for most measures and <= for
# the lower-is-better ones below. A profile setting a measure to None doesn't rate it.
_BASE_THRESHOLDS = {
    "gross_margin": (0.40, 0.20),
    "operating_margin": (0.15, 0.05),
    "roe": (0.15, 0.08),
    "fcf_margin": (0.10, 0.0),
    "debt_to_equity": (1.0, 2.0),
    "current_ratio": (1.2, 0.7),
    "debt_to_cash_flow": (2.5, 5.0),  # total debt / operating cash flow, in years
    "revenue_growth": (0.05, -0.02),  # annual rate over up to 3 years
    "net_income_growth": (0.05, -0.02),  # year over year
}
_LOWER_IS_BETTER = {"debt_to_equity", "debt_to_cash_flow"}

PROFILES = {
    "default": _BASE_THRESHOLDS,
    "low_margin": {**_BASE_THRESHOLDS, "gross_margin": (0.30, 0.15), "operating_margin": (0.10, 0.04),
                   "fcf_margin": (0.05, 0.0), "current_ratio": (1.0, 0.6)},
    "cyclical": {**_BASE_THRESHOLDS, "gross_margin": (0.25, 0.10), "operating_margin": (0.10, 0.03),
                 "roe": (0.12, 0.06), "fcf_margin": (0.08, 0.0)},
    # Utilities and REITs run on debt and heavy capex by design: looser debt bands, and no
    # current ratio or free-cash-flow margin (both routinely look poor in a healthy one).
    "leveraged": {**_BASE_THRESHOLDS, "debt_to_equity": (2.0, 4.0), "roe": (0.06, 0.025),
                  "current_ratio": None, "fcf_margin": None, "debt_to_cash_flow": (5.0, 8.0)},
}

PROFILE_DESCRIPTIONS = {
    "default": "standard thresholds",
    "low_margin": "thresholds for low-margin businesses (retail, distribution, hardware, autos)",
    "cyclical": "thresholds for cyclical businesses (industrials, energy, materials)",
    "leveraged": "looser debt thresholds, no current-ratio or cash-flow-margin test (utilities, real estate)",
}

# All 11 Yahoo Finance sectors; a missing or new sector name falls back to "default".
SECTOR_PROFILE = {
    "Technology": "default", "Healthcare": "default", "Communication Services": "default",
    "Financial Services": "default",
    "Consumer Defensive": "low_margin", "Consumer Cyclical": "low_margin",
    "Industrials": "cyclical", "Energy": "cyclical", "Basic Materials": "cyclical",
    "Utilities": "leveraged", "Real Estate": "leveraged",
}
# An industry containing one of these is low-margin whatever its sector says (a hardware
# maker sits in Technology but earns a distributor's margins).
_LOW_MARGIN_INDUSTRY_WORDS = ("Hardware", "Distribution", "Grocery", "Discount Stores", "Auto Manufacturers")

MIN_MEASURES = 3  # fewer usable measures than this and the company is "not rated"
_GREEN_MEAN, _YELLOW_MEAN = 1.5, 0.75
_GROWTH_YEARS = 3


def profile_for(sector, industry=None) -> str:
    """Name of the threshold profile for a company: an industry keyword wins over the sector."""
    if isinstance(industry, str) and any(word in industry for word in _LOW_MARGIN_INDUSTRY_WORDS):
        return "low_margin"
    return SECTOR_PROFILE.get(sector, "default")


def _num(value):
    """None for a missing or NaN figure, else the value."""
    return None if value is None or pd.isna(value) else value


def _as_dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _band(value, thresholds, lower_is_better=False):
    green_at, yellow_at = thresholds
    if lower_is_better:
        return GREEN if value <= green_at else YELLOW if value <= yellow_at else RED
    return GREEN if value >= green_at else YELLOW if value >= yellow_at else RED


def _light_from_mean(mean):
    return GREEN if mean >= _GREEN_MEAN else YELLOW if mean >= _YELLOW_MEAN else RED


def _mean_light(lights):
    """(light, mean score) of a list of lights, ignoring None; (None, None) when none scored."""
    scored = [_SCORE[light] for light in lights if light]
    if not scored:
        return None, None
    mean = sum(scored) / len(scored)
    return _light_from_mean(mean), mean


def _annual_growth(values):
    """Compound annual growth over up to the last 3 years of a series, or None when there
    are fewer than 2 usable years or the first/last year isn't positive (a growth rate
    from or to a loss is meaningless)."""
    usable = [v for v in map(_num, values) if v is not None]
    if len(usable) < 2:
        return None
    years = min(_GROWTH_YEARS, len(usable) - 1)
    first, last = usable[-1 - years], usable[-1]
    if first <= 0 or last <= 0:
        return None
    return (last / first) ** (1 / years) - 1


def _net_income_growth(values, thresholds):
    """(light, text) for latest-vs-prior-year net income, or None. A loss turning into a
    profit is yellow ("turned profitable": can't tell a one-off from a real recovery);
    still not positive is red."""
    if len(values) < 2:
        return None
    latest, prior = _num(values[-1]), _num(values[-2])
    if latest is None or prior is None:
        return None
    yoy = calculations.yoy_pct(values)
    if yoy is None:
        return (YELLOW, "turned profitable") if latest > 0 else (RED, "still negative")
    return _band(yoy / 100, thresholds), f"{yoy:+.1f}%"


def _earnings_consistency(values):
    """(light, text) for how many of the stored years were profitable, or None with under 3
    years: no loss year is green, one loss year with the latest year profitable is yellow,
    anything else red."""
    usable = [v for v in map(_num, values) if v is not None]
    if len(usable) < 3:
        return None
    losses = sum(1 for v in usable if v <= 0)
    text = f"{len(usable) - losses}/{len(usable)} profitable years"
    if losses == 0:
        return GREEN, text
    if usable[-1] > 0 and losses == 1:
        return YELLOW, text
    return RED, text


def _last(values):
    return _num(values[-1]) if values else None


def assess_health(income, balance, cashflow, sector=None, industry=None) -> dict | None:
    """Rate one company from its three statement dicts ({row_label: {"YYYY-MM-DD": value}},
    see market_data._statement_to_dict).

    None when there are no income/balance statements at all (an ETF or fund: nothing to
    rate -- distinct from a rated-but-inconclusive company, whose "overall" is None).
    Otherwise a dict:

    - overall: GREEN / YELLOW / RED, or None = not rated (fewer than MIN_MEASURES measures)
    - profile: which threshold profile was used (see PROFILES)
    - partial: True for a bank/lender/fund-shaped statement (no Gross Profit and no Current
      Assets), which is rated on ROE and growth only because margin and liquidity ratios
      don't mean the same thing there
    - n_measures: how many measures were rated
    - flags: hard flags -- FLAG_LOSS (latest net income below zero), FLAG_BURN (latest free
      cash flow below zero, where the profile rates cash flow)
    - reds: names of the red measures, in display order
    - groups: {PROFIT|DEBT|GROWTH: {"light": ..., "measures": [{"name", "value" (display
      text), "light"}]}}; a group with no measures has light None

    Overall = mean of the group means (green 2, yellow 1, red 0; >=1.5 green, >=0.75
    yellow), then: any red group holds it at yellow at best; losing money AND burning cash
    forces red; either alone holds it at yellow at best."""
    income, balance, cashflow = _as_dict(income), _as_dict(balance), _as_dict(cashflow)
    if not income and not balance:
        return None

    profile_name = profile_for(sector, industry)
    thresholds = PROFILES[profile_name]
    ratios = calculations.compute_key_ratios(income, balance)
    _, revenue = calculations.statement_series(income, "Total Revenue")
    _, net_income = calculations.statement_series(income, "Net Income")
    _, free_cash_flow = calculations.statement_series(cashflow, "Free Cash Flow")
    _, operating_cash_flow = calculations.statement_series(cashflow, "Operating Cash Flow")
    latest_fcf = _last(free_cash_flow)
    equity = _num(calculations.latest_statement_value(balance, "Stockholders Equity"))
    debt = _num(calculations.latest_statement_value(balance, "Total Debt"))
    partial = (
        not _num(calculations.latest_statement_value(income, "Gross Profit"))
        and not _num(calculations.latest_statement_value(balance, "Current Assets"))
    )
    negative_equity = equity is not None and equity <= 0

    groups = {PROFIT: [], DEBT: [], GROWTH: []}

    def rate(group, name, key, value, fmt):
        if thresholds[key] is None or _num(value) is None:
            return
        light = _band(value, thresholds[key], lower_is_better=key in _LOWER_IS_BETTER)
        groups[group].append({"name": name, "value": fmt.format(value), "light": light})

    if not partial:
        rate(PROFIT, "Gross margin", "gross_margin", ratios["gross_margin"], "{:.1%}")
        rate(PROFIT, "Operating margin", "operating_margin", ratios["operating_margin"], "{:.1%}")
    if not negative_equity:
        rate(PROFIT, "Return on equity", "roe", ratios["return_on_equity"], "{:.1%}")
    if not partial:
        rate(PROFIT, "Free cash flow margin", "fcf_margin",
             calculations.safe_divide(latest_fcf, _last(revenue)), "{:.1%}")
        if negative_equity:
            groups[DEBT].append({"name": "Debt / equity", "value": "negative equity", "light": RED})
        else:
            rate(DEBT, "Debt / equity", "debt_to_equity", ratios["debt_to_equity"], "{:.2f}x")
        rate(DEBT, "Current ratio", "current_ratio", ratios["current_ratio"], "{:.2f}x")
        latest_ocf = _last(operating_cash_flow)
        if debt is not None and operating_cash_flow:
            if debt <= 0:
                groups[DEBT].append({"name": "Debt / operating cash flow", "value": "no debt", "light": GREEN})
            elif latest_ocf is not None and latest_ocf <= 0:
                groups[DEBT].append({"name": "Debt / operating cash flow", "value": "no operating cash flow",
                                     "light": RED})
            elif latest_ocf is not None:
                rate(DEBT, "Debt / operating cash flow", "debt_to_cash_flow", debt / latest_ocf, "{:.1f} yrs")

    rate(GROWTH, "Revenue growth (3y)", "revenue_growth", _annual_growth(revenue), "{:+.1%}/yr")
    growth = _net_income_growth(net_income, thresholds["net_income_growth"])
    if growth:
        groups[GROWTH].append({"name": "Net income growth", "value": growth[1], "light": growth[0]})
    consistency = _earnings_consistency(net_income)
    if consistency:
        groups[GROWTH].append({"name": "Earnings consistency", "value": consistency[1], "light": consistency[0]})

    group_lights, group_means = {}, []
    for name, measures in groups.items():
        light, mean = _mean_light([m["light"] for m in measures])
        group_lights[name] = light
        if mean is not None:
            group_means.append(mean)
    n_measures = sum(len(measures) for measures in groups.values())

    loss = _last(net_income) is not None and _last(net_income) < 0
    burn = not partial and thresholds["fcf_margin"] is not None and latest_fcf is not None and latest_fcf < 0
    flags = ([FLAG_LOSS] if loss else []) + ([FLAG_BURN] if burn else [])

    if n_measures < MIN_MEASURES or not group_means:
        overall = None
    else:
        overall = _light_from_mean(sum(group_means) / len(group_means))
        if overall == GREEN and RED in group_lights.values():
            overall = YELLOW
        if loss and burn:
            overall = RED
        elif (loss or burn) and overall == GREEN:
            overall = YELLOW

    return {
        "overall": overall,
        "profile": profile_name,
        "partial": partial,
        "n_measures": n_measures,
        "flags": flags,
        "reds": [m["name"] for measures in groups.values() for m in measures if m["light"] == RED],
        "groups": {name: {"light": group_lights[name], "measures": groups[name]} for name in GROUPS},
    }


def format_health_cell(result) -> str:
    """Table-cell text: "🟢 Healthy" / "🟡 Mixed" / "🔴 Weak", " (partial)" appended for a
    bank/lender/fund-shaped statement; "⚪ Not rated" when too few measures; "—" when there
    are no statements to rate (ETF/fund)."""
    if result is None:
        return "—"
    overall = result["overall"]
    if overall is None:
        return "⚪ Not rated"
    return f"{DOTS[overall]} {LABELS[overall]}" + (" (partial)" if result["partial"] else "")


def health_reasons(result) -> list[str]:
    """Why the verdict isn't cleaner: hard flags first, then each red measure with its
    value ("Debt / equity: 3.10x"). Empty for None, or when nothing is red or flagged."""
    if result is None:
        return []
    reds = {m["name"]: m["value"] for g in result["groups"].values() for m in g["measures"] if m["light"] == RED}
    return [flag.capitalize() for flag in result["flags"]] + [f"{name}: {value}" for name, value in reds.items()]


def describe_health(result) -> str:
    """Multi-line text a chat bot can relay: the verdict, each group with its measures, the
    reasons it isn't cleaner, the rule profile used, and the rule-of-thumb caveat."""
    if result is None:
        return "Health: not available -- no financial statements are stored for this symbol (likely an ETF or fund)."
    overall = result["overall"]
    if overall is None:
        head = (f"Health: not rated -- only {result['n_measures']} usable measure"
                f"{'s' if result['n_measures'] != 1 else ''} in the stored statements (at least {MIN_MEASURES} needed).")
    else:
        head = f"Health: {LABELS[overall]}."
        if result["partial"]:
            head += " Partial: the statements look like a bank, lender or fund, so it is rated on ROE and growth only."
    lines = [head]
    for name in GROUPS:
        group = result["groups"][name]
        if not group["measures"]:
            lines.append(f"- {name}: no measures available")
            continue
        detail = "; ".join(f"{m['name']} {m['value']} ({m['light']})" for m in group["measures"])
        lines.append(f"- {name}: {LABELS[group['light']]} -- {detail}")
    reasons = health_reasons(result)
    if reasons:
        lines.append("Watch: " + "; ".join(reasons) + ".")
    lines.append(
        f"Rules used: {result['profile']} profile ({PROFILE_DESCRIPTIONS[result['profile']]}). "
        "A rule-of-thumb read of the stored annual statements, not investment advice."
    )
    return "\n".join(lines)
