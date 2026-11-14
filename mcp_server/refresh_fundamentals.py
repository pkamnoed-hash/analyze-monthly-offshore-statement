"""V4.21 -- standalone daily refresh of fundamentals_cache (financial statements,
valuation ratios), meant to run unattended via VPS cron, NOT through Streamlit. Runs
the exact same fetch-with-fallback logic Monitor Stocks'/Company Fundamentals' own
"Refresh now" button calls (app_pages/monitor_stocks.py::_refresh_fundamentals_live)
against every currently-held symbol.

Deliberately scheduled 12 hours apart from refresh_market_profile.py (see
mcp_server/README.md for the exact cron times) -- fundamentals barely change day to
day (quarterly data), so there's no need to run this at the same time and double the
yfinance load in one burst; staggering spreads out the rate-limit risk this app has
hit before.

Credentials and failure handling: same as refresh_market_profile.py -- loads
mcp_server/.env, never raises, a failed live fetch falls back to the last-captured
cached value per symbol (calculations.apply_fundamentals_fallback).
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

from core import calculations, db, market_data  # noqa: E402


def main():
    positions = calculations.compute_current_positions(db.fetch_trades())
    if positions.empty:
        print("No current holdings -- nothing to refresh.")
        return

    symbols = positions["Symbol"].tolist()
    live = market_data.fetch_fundamentals(symbols)
    cached = db.fetch_fundamentals_cache()
    merged = calculations.apply_fundamentals_fallback(live, cached)

    fresh_rows = merged[~merged["Stale"] & merged["Current Price"].notna()]
    if not fresh_rows.empty:
        db.save_fundamentals_cache(fresh_rows.drop(columns=["Stale", "Fetched At"]).to_dict("records"))

    stale_count = int(merged["Stale"].sum())
    print(f"Refreshed {len(fresh_rows)}/{len(symbols)} symbol(s); {stale_count} stale/fallback.")


if __name__ == "__main__":
    main()
