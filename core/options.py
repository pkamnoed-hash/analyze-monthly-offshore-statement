"""V4.22 -- OCC-style option symbol parsing/building, no Streamlit/DB.

The compound symbol format every real screenshot and the original broker-statement
seed data already use, e.g. "MNST261218C00045000": root symbol, 6-digit expiration
(YYMMDD), C/P, 8-digit strike (implied 3 decimal places, i.e. strike * 1000).
"""

import re

import pandas as pd

_OCC_PATTERN = re.compile(r"^([A-Z]+)(\d{2})(\d{2})(\d{2})([CP])(\d{8})$")


def parse_option_symbol(symbol: str) -> dict | None:
    """Decodes an OCC-style compound symbol into its parts. Returns None for
    anything that doesn't match -- never raises, since callers (e.g. the
    migration script scanning every row in `trades`) need to tell a real option
    symbol apart from an ordinary stock ticker just by trying this."""
    if not isinstance(symbol, str):
        return None
    match = _OCC_PATTERN.match(symbol)
    if not match:
        return None
    root, yy, mm, dd, cp, strike_digits = match.groups()
    try:
        expiration_date = pd.Timestamp(year=2000 + int(yy), month=int(mm), day=int(dd))
    except ValueError:
        return None
    return {
        "underlying": root,
        "expiration_date": expiration_date,
        "option_type": "Call" if cp == "C" else "Put",
        "strike": int(strike_digits) / 1000,
    }


def build_option_symbol(underlying: str, expiration_date, option_type: str, strike: float) -> str:
    """Inverse of parse_option_symbol() -- builds the compound symbol from its
    decomposed parts, used by the entry form (so a manually-typed trade gets the
    same `symbol` a real screenshot/seed row would) and the migration script."""
    exp = pd.Timestamp(expiration_date)
    cp = "C" if option_type.lower().startswith("c") else "P"
    strike_digits = round(strike * 1000)
    return f"{underlying.upper()}{exp.strftime('%y%m%d')}{cp}{strike_digits:08d}"
