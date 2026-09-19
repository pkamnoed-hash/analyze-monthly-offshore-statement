"""V4.15 -- one live Yahoo Finance fetch of a single symbol's fundamentals, saved to
fundamentals_cache, for the Hermes MCP server's get_company_fundamentals tool when a
symbol has never been stored (first lookup) or the user asks for fresh numbers.

The same rule the app already follows everywhere it captures fundamentals
(cached_db.fundamentals_summary's auto-capture, Company Fundamentals' "Refresh now"):
only a fetch that genuinely succeeded is saved. A fetch is genuine when it has a real
Current Price -- market_data.fetch_fundamentals()'s own success signal -- so an
unresolvable symbol, a typo, or a temporary Yahoo problem saves NOTHING, and can never
overwrite a good stored row with a blank one. Kept out of the MCP file so this write
path is unit tested (tests/test_fundamentals_capture.py) with an injected connection and
a fake yfinance module.
"""

from core import db, market_data


def capture_fundamentals(symbol: str, *, conn=None, yf_module=None) -> bool:
    """Fetches `symbol` live and upserts its fundamentals_cache row. Returns True if a
    row was saved, False if the fetch failed (nothing written, any existing row for
    the symbol left exactly as it was). `conn`/`yf_module` are injection points for
    tests; production callers omit both. Only ever writes to fundamentals_cache."""
    live = market_data.fetch_fundamentals([symbol], yf_module=yf_module)
    successful = live[live["Current Price"].notna()]
    if successful.empty:
        return False
    db.save_fundamentals_cache(successful.to_dict("records"), conn=conn)
    return True
