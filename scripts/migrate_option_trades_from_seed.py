"""One-time migration: copies the option trades already sitting in `trades`
(OCC-style symbols from the original broker-statement seed import, e.g.
"ONDS260130P00012000") into the new `option_trades` table, so Option Monitor's
win-ratio/P&L view includes them from day one.

Deliberately NOT a deletion -- the original 6 rows stay exactly where they are in
`trades` (Dashboard's Realized P/L is unchanged), per the user's own confirmed
choice (docs/ROADMAP.md's V4.22 section). Idempotent -- a symbol already present
in option_trades (source='migrated-seed') is skipped, so re-running this script is
always safe.

Usage:
    python scripts/migrate_option_trades_from_seed.py
        migrates whatever .streamlit/secrets.toml points at (dev, normally)
    python scripts/migrate_option_trades_from_seed.py --secrets .streamlit/secrets.prod.toml.bak
        migrates the real prod database using the saved prod credentials
"""

import argparse
import os
import sys
import tomllib
from urllib.parse import urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def main():
    parser = argparse.ArgumentParser(description="Migrate OCC-style option trades out of `trades` into `option_trades`.")
    parser.add_argument(
        "--secrets", default=os.path.join(ROOT, ".streamlit", "secrets.toml"),
        help="TOML file holding TURSO_DATABASE_URL / TURSO_AUTH_TOKEN (default: .streamlit/secrets.toml)",
    )
    args = parser.parse_args()

    with open(args.secrets, "rb") as f:
        secrets = tomllib.load(f)
    os.environ["TURSO_DATABASE_URL"] = secrets["TURSO_DATABASE_URL"]
    os.environ["TURSO_AUTH_TOKEN"] = secrets["TURSO_AUTH_TOKEN"]

    env_label = secrets.get("APP_ENV", "prod")
    host = urlparse(secrets["TURSO_DATABASE_URL"]).hostname
    print(f"Migrating {host} (APP_ENV = {env_label}) ...")

    from core import db  # noqa: E402  (needs the env bridge above first)
    from core.options import parse_option_symbol  # noqa: E402

    conn = db.get_connection()
    try:
        db.init_db(conn=conn)

        trades = db.fetch_trades(conn=conn)
        existing_migrated_symbols = set(
            db.fetch_option_trades(conn=conn).query("source == 'migrated-seed'")["symbol"]
        )

        decoded = trades["Symbol"].apply(parse_option_symbol)
        option_rows = trades[decoded.notna()].copy()
        option_rows["_decoded"] = decoded[decoded.notna()]

        migrated_symbols, inserted = 0, 0
        for symbol, group in option_rows.groupby("Symbol"):
            if symbol in existing_migrated_symbols:
                print(f"  skip {symbol}: already migrated")
                continue
            group = group.sort_values("Trade Date")
            if len(group) != 2:
                print(f"  SKIP {symbol}: expected exactly 2 rows (one open, one close), found {len(group)} -- not migrated, needs a manual look")
                continue

            decoded_fields = group.iloc[0]["_decoded"]
            for position_effect, (_, row) in zip(["open", "close"], group.iterrows()):
                db.insert_option_trade(
                    trade_date=row["Trade Date"].strftime("%Y-%m-%d"),
                    symbol=symbol,
                    underlying=decoded_fields["underlying"],
                    option_type=decoded_fields["option_type"],
                    strike=decoded_fields["strike"],
                    expiration_date=decoded_fields["expiration_date"].strftime("%Y-%m-%d"),
                    side=row["Side"],
                    position_effect=position_effect,
                    contracts=abs(row["Quantity"]),
                    price=row["Price"],
                    source="migrated-seed",
                    conn=conn,
                )
                inserted += 1
            migrated_symbols += 1
            print(f"  migrated {symbol} (2 rows)")
    finally:
        conn.close()

    print(f"\nDone: {migrated_symbols} symbol(s) migrated, {inserted} row(s) inserted.")


if __name__ == "__main__":
    main()
