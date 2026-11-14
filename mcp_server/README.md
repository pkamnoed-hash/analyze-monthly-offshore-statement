# Portfolio MCP server

An MCP server exposing read-only(-in-spirit) portfolio Q&A tools to the
Hermes Agent "Rich" Telegram bot -- backed directly by this repo's own
`core/db.py` / `core/calculations.py`, not a copy. Full design in
`docs/ROADMAP.md`'s V4.13 and V4.15 sections.

## Tools

| Tool | Answers |
|---|---|
| `get_holdings_count` | How many symbols are held |
| `get_upcoming_ex_dates` | Held symbols with an Ex-Date this month |
| `get_holdings_pl` | Current-holdings P/L (live) |
| `get_lifetime_pl` | All-time P/L (matches Dashboard) |
| `get_reference_line_status` | Held symbols that passed their nearest support/resistance line |
| `get_company_fundamentals(symbol, refresh=false)` | One ticker's profile, Analyst Target verdict, latest-year KPIs, key ratios and (V4.16) the Health rating; any ticker, held or not (V4.15) |
| `get_holdings_health` | (V4.16) Every current holding rated Weak / Mixed / Healthy with the reasons; ETFs/funds and not-yet-stored symbols named. Read-only, no live fetch |
| `record_trades_batch(trades, confirm=false)` | (V4.19) Records real buy/sell trades, e.g. from a photo of Dime!'s Activity list. `confirm=false` previews (what's new vs. already-recorded, writes nothing); `confirm=true` writes only the new rows |
| `record_dividends_batch(dividends, confirm=false)` | (V4.19) Same two-call pattern, for dividends/interest/capital distributions |
| `get_rebalance_plan` | (V4.20) The current Rebalance & Reallocate plan: $ amount, % allocated/remaining, each symbol's % Reinvest/Invest $/Bought status, and projected Div Contrib %/Expected Div/Mo. Read-only |
| `update_rebalance_plan(amount, items, confirm=false)` | (V4.20) Edits the plan's $ amount and/or any symbol's % Reinvest/Bought? tick. Same two-call confirm pattern |
| `reset_rebalance_plan(confirm=false)` | (V4.20) Abandons the current plan and starts a fresh blank one, same as the page's Reset plan button |

Writes are limited to `reference_lines` (`get_reference_line_status`),
`fundamentals_cache` (`get_company_fundamentals`), and, as of V4.19, `trades`/
`dividends` through the two confirm-gated batch tools above -- a deliberate,
narrow exception, never a free pass (see `core/trade_entry.py`/
`core/dividend_entry.py` for the two-call contract and duplicate-matching
logic those tools depend on). `get_holdings_health` writes nothing.
As of V4.20, `rebalance_plans`/`rebalance_plan_items` through
`update_rebalance_plan`/`reset_rebalance_plan` (same confirm-gated contract,
enforced in `core/rebalance_entry.py`) -- these never touch `trades`/
`dividends`, only the plan itself (a reminder of what to buy).
`save_symbol_types` remains categorically forbidden -- no tool here touches it.
See `docs/ARCHITECTURE.md` for the full write-boundary reasoning.

## Local testing (before touching the VPS)

```bash
python -m venv mcp_server/.venv
mcp_server/.venv/Scripts/pip install -r mcp_server/requirements.txt   # Windows
# mcp_server/.venv/bin/pip install -r mcp_server/requirements.txt     # macOS/Linux

cp mcp_server/.env.example mcp_server/.env
# edit mcp_server/.env -- for local testing, the dev Turso credentials from
# .streamlit/secrets.toml are fine; use PROD only on the real VPS deployment.

mcp_server/.venv/Scripts/mcp dev mcp_server/portfolio_mcp.py   # interactive MCP Inspector
```

## VPS deployment (alongside the existing 4 Hermes bot profiles on `hermes-vps`)

1. `git clone` this repo onto the VPS (or `git pull` if already cloned).
2. ```bash
   cd analyze-monthly-offshore-statement
   python3 -m venv mcp_server/.venv
   mcp_server/.venv/bin/pip install -r mcp_server/requirements.txt
   ```
3. `cp mcp_server/.env.example mcp_server/.env` and fill in **prod** Turso
   credentials -- see the comment in `.env.example` for why this must be a
   prod-pointed, ideally dedicated token.
4. Add to `/root/.hermes/profiles/rich/config.yaml`:
   ```yaml
   mcp_servers:
     portfolio:
       command: "/path/to/analyze-monthly-offshore-statement/mcp_server/.venv/bin/python"
       args: ["/path/to/analyze-monthly-offshore-statement/mcp_server/portfolio_mcp.py"]
   ```
5. Add a short note to the `rich` profile's `SOUL.md` describing the tools
   this server exposes and when to reach for them.
   Routing needs to be explicit -- Rich will otherwise improvise (on 19 Sep 2026
   it answered a "health of <stock>" question with its own Yahoo Finance
   script). Suggested wording, adapted to the tools present:
   ```
   For questions about a company's valuation (overvalued or undervalued), key
   ratios, revenue, profit, debt, business profile, or how healthy / financially
   strong / weak it is, call get_company_fundamentals with the ticker. It works
   for any ticker, not only holdings. For "which of my stocks look weak /
   healthy" or the health of the whole portfolio, call get_holdings_health.
   Use these tools instead of fetching data yourself: never write or run your own
   yfinance script for these questions, because your numbers would not match the
   user's app. Their answers state the date of the data; if the user wants
   current numbers, call get_company_fundamentals again with refresh=true. ETFs
   and funds have no financial statements, so say so plainly. Call one tool at a
   time.
   ```
6. `hermes -p rich gateway restart`.
7. Ask Rich a real question on Telegram and confirm the answer matches what
   the Streamlit app itself shows for the same data.
   Check Rich's **log, not just the reply**: the tool call is recorded in the
   profile's `state.db` (`messages.tool_calls`, as `tool_call` with
   `mcp__portfolio__<tool name>`), and a reply can imitate a tool's wording.

## Daily refresh cron (V4.21)

Two standalone scripts, run unattended by cron on the VPS -- NOT through Hermes or
Streamlit, just a plain `.venv` python process reusing `core/market_data.py`/
`core/calculations.py`/`core/db.py` directly, the same functions Monitor Stocks'
"Refresh now" button calls, against every currently-held symbol:

| Script | Mirrors | Scheduled (Thailand time) |
|---|---|---|
| `refresh_market_profile.py` | `_refresh_stock_profile_live` (price/dividend yield/ex-date/history) | 11:00 AM daily |
| `refresh_fundamentals.py` | `_refresh_fundamentals_live` (financial statements/ratios) | 11:00 PM daily |

Staggered 12 hours apart on purpose -- fundamentals barely change day to day, so
running both back-to-back would just double the yfinance load in one burst for no
benefit. Each writes to `market_profile_cache`/`fundamentals_cache` and prints one
summary line (`"Refreshed X/Y symbol(s); Z stale/fallback."`) for the cron log; a
failed live attempt per symbol falls back to the last-captured cached value, same as
the button, so a bad run (most likely cause: Yahoo rate-limiting) never blanks good
data.

Crontab entries (confirm the VPS's actual timezone with `timedatectl` first -- these
assume a UTC system clock; ICT is fixed UTC+7 year-round, no DST):

```cron
0 4  * * * cd /root/repos/analyze-monthly-offshore-statement/mcp_server && .venv/bin/python refresh_market_profile.py >> refresh_profile.log 2>&1
0 16 * * * cd /root/repos/analyze-monthly-offshore-statement/mcp_server && .venv/bin/python refresh_fundamentals.py >> refresh_fundamentals.log 2>&1
```

Every page that reads this cached data gained a 5-minute ttl in V4.21
(`_cached_read_stock_profile_from_db` in 4 pages, `cached_db.py::fundamentals_summary`)
-- without it, a write from this separate cron process would sit invisible in an
already-running Streamlit session until it restarted (same gap V4.19.1 fixed for
trades/dividends). "Refresh now" is unchanged and still available as a manual
override.
