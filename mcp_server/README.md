# Portfolio MCP server

An MCP server exposing read-only(-in-spirit) portfolio Q&A tools to the
Hermes Agent "Rich" Telegram bot -- backed directly by this repo's own
`core/db.py` / `core/calculations.py`, not a copy. Full design in
`docs/ROADMAP.md`'s V4.13 section.

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
