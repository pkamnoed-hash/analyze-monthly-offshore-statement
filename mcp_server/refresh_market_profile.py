"""V4.21 -- standalone daily refresh of market_profile_cache (price, dividend yield,
ex-date, 90-day history), meant to run unattended via VPS cron, NOT through Streamlit.
Runs the exact same fetch-with-fallback logic Monitor Stocks' own "Refresh now" button
calls (app_pages/monitor_stocks.py::_refresh_stock_profile_live) against every
currently-held symbol -- reusing core/ functions directly rather than duplicating their
logic, same precedent mcp_server/portfolio_mcp.py already set for a non-Streamlit
process importing core/.

Credentials: loaded from mcp_server/.env the same way portfolio_mcp.py does (prod on
the VPS, dev for local testing) -- see mcp_server/README.md for the exact cron wiring.
Prints one summary line to stdout for the cron log; never raises -- a failed live fetch
falls back to the last-captured cached value per symbol (calculations.
apply_market_profile_fallback), same as the button, so a bad run degrades gracefully
instead of blanking good data.

This script only writes market_profile_cache. It does NOT call .clear() on any
Streamlit @st.cache_data cache (there is no running Streamlit process here) -- the
pages that read this data gained their own 5-minute ttl in V4.21 instead, the same
mechanism V4.19.1 already used so an external write surfaces without a manual click or
app restart.
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
    live = market_data.fetch_stock_profile(symbols)
    cached = db.fetch_market_profile_cache()
    merged = calculations.apply_market_profile_fallback(live, cached)

    fresh_rows = merged[~merged["Stale"] & merged["Latest Price"].notna()]
    if not fresh_rows.empty:
        db.save_market_profile_cache(fresh_rows.drop(columns=["Stale", "Fetched At"]).to_dict("records"))

    stale_count = int(merged["Stale"].sum())
    print(f"Refreshed {len(fresh_rows)}/{len(symbols)} symbol(s); {stale_count} stale/fallback.")


if __name__ == "__main__":
    main()
