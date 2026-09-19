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

Writes are limited to `reference_lines` (`get_reference_line_status`) and
`fundamentals_cache` (`get_company_fundamentals`); never trades, dividends or
symbol types. `get_holdings_health` writes nothing. See `docs/ARCHITECTURE.md` for the write-boundary reasoning.

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
