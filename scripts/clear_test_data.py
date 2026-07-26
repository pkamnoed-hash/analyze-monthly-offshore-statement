"""Delete test/mock trades and dividends entered through Record Trade / Record
Dividend while trying the app out, without touching the real seeded history
(source='seed' rows are never touched by this script).

Usage:
    python scripts/clear_test_data.py                  # clears 'manual' and 'slip' sources
    python scripts/clear_test_data.py --source manual   # clears just one source
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import db  # noqa: E402  (needs sys.path set up above)


def main():
    sources = ["manual", "slip"]
    if "--source" in sys.argv:
        sources = [sys.argv[sys.argv.index("--source") + 1]]

    conn = db.get_connection()
    for source in sources:
        trades_before = conn.execute("SELECT COUNT(*) FROM trades WHERE source=?", (source,)).fetchone()[0]
        dividends_before = conn.execute("SELECT COUNT(*) FROM dividends WHERE source=?", (source,)).fetchone()[0]
        db.delete_trades_by_source(source, conn=conn)
        db.delete_dividends_by_source(source, conn=conn)
        print(f"source='{source}': deleted {trades_before} trade row(s), {dividends_before} dividend row(s).")
    conn.close()


if __name__ == "__main__":
    main()
