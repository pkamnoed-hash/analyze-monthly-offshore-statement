"""Streamlit-cached wrappers around core.db's trades/dividends/symbol_types reads,
plus matching invalidate functions. Lives here (project root, not core/) since it
needs a Streamlit import, which core/ deliberately avoids (see CLAUDE.md) -- and not
in app_pages/ since it's shared infrastructure, not a page.

v4.5 -- these three reads were previously called uncached on every single page load
(app_pages/dashboard.py's own old comment: "a trade just recorded should show up
immediately"), meaning every navigation/rerun paid a real Turso round trip per read,
even when nothing had changed -- one of three real causes behind the app's "always
reloads" feeling (see docs/ROADMAP.md V4.5). Cached here with no ttl (held for the
life of the session) and invalidated explicitly by whichever write actually changed
that table, same pattern this app already uses for yfinance's "Refresh now" button
(_cached_fetch_stock_profile.clear() in app_pages/monitor_stocks.py) -- so a save is
never stale, not even briefly, while plain navigation stops paying for a redundant
fetch.
"""
import streamlit as st

from core import db


@st.cache_data
def cached_fetch_trades():
    return db.fetch_trades()


@st.cache_data
def cached_fetch_dividends():
    return db.fetch_dividends()


@st.cache_data
def cached_fetch_symbol_types():
    return db.fetch_symbol_types()


def invalidate_trades():
    cached_fetch_trades.clear()


def invalidate_dividends():
    cached_fetch_dividends.clear()


def invalidate_symbol_types():
    cached_fetch_symbol_types.clear()
