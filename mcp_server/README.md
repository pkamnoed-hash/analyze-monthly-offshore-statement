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
| `get_company_fundamentals(symbol, refresh=false)` | One ticker's profile, Analyst Target verdict, latest-year KPIs and key ratios; any ticker, held or not (V4.15) |

Writes are limited to `reference_lines` (`get_reference_line_status`) and
`fundamentals_cache` (`get_company_fundamentals`); never trades, dividends or
symbol types. See `docs/ARCHITECTURE.md` for the write-boundary reasoning.

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
6. `hermes -p rich gateway restart`.
7. Ask Rich a real question on Telegram and confirm the answer matches what
   the Streamlit app itself shows for the same data.
