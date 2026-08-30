"""One-off: stamp provenance onto bars written before `source` existed.

The schema migration deliberately leaves old rows NULL - a row cannot know
where it came from, and a migration that stamped every one of them 'real'
would certify exactly the demo bars the column exists to expose. Deciding
provenance for an existing database is therefore a data job, done here,
with the evidence for each verdict written down next to it.

    python scripts/backfill_bar_source.py          # dry run, prints the plan
    python scripts/backfill_bar_source.py --apply

Re-runnable: it only ever writes rows whose source is still NULL.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from candle_agent import db  # noqa: E402

REAL = "alpaca"
DEMO = db.SYNTHETIC

# Verdicts from the 2026-08-30 audit of the bars table. Each is a claim
# about where a series came from, defensible without looking at prices:
#
#   real - the series has the shape a real session leaves behind: bars
#          only on weekdays, gaps overnight and at weekends, and several
#          separate day-segments rather than one unbroken run. MSFT 1m was
#          checked hardest - a 200-bar overlap with the live Alpaca API
#          matched OHLC exactly, with no stored bar missing upstream.
#
#   demo - BTCUSDT is synthetic on provenance alone, which is stronger
#          than any shape argument: Binance answers HTTP 451 from this
#          host, so no BTCUSDT bar here can ever have been fetched from
#          it. It is also one unbroken 3683-bar run, which a real feed
#          does not produce.
#
# TSLA is deliberately absent. Every TSLA interval is now mixed at row
# level: the old demo block plus real bars backfilled on top of it, at
# timestamps that overlap. Nothing structural separates them row by row,
# so stamping the series either way would be a guess. Bars are derived
# data - the honest repair is delete_bars() + refill, not a label.
MANIFEST = {
    ("A", "1m"): REAL,
    ("AAPB", "1m"): REAL,
    ("AAPB", "1d"): REAL,
    ("AAPL", "1m"): REAL,
    ("AAPL", "5m"): REAL,
    ("MSFT", "1m"): REAL,
    ("MSFT", "5m"): REAL,
    ("MSFT", "15m"): REAL,
    ("MSFT", "1h"): REAL,
    ("BTCUSDT", "1m"): DEMO,
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="write the changes (default is a dry run)")
    args = ap.parse_args()

    with db.conn() as c:
        series = c.execute(
            "SELECT symbol, interval, COUNT(*) n, "
            "SUM(source IS NULL) unstamped FROM bars "
            "GROUP BY symbol, interval ORDER BY symbol, interval"
        ).fetchall()

        planned = unknown = 0
        print(f"{'series':16s} {'rows':>6s} {'unstamped':>10s}  verdict")
        for r in series:
            key = (r["symbol"], r["interval"])
            verdict = MANIFEST.get(key)
            label = verdict or "UNKNOWN - not in manifest, left NULL"
            print(f"{r['symbol'] + ' ' + r['interval']:16s} {r['n']:6d} "
                  f"{r['unstamped']:10d}  {label}")
            if verdict is None:
                unknown += r["unstamped"]
            else:
                planned += r["unstamped"]

        print(f"\n{planned} rows to stamp, {unknown} left NULL (unknown provenance)")
        if not args.apply:
            print("dry run - re-run with --apply to write")
            return

        written = 0
        for (symbol, interval), verdict in MANIFEST.items():
            written += c.execute(
                "UPDATE bars SET source=? "
                "WHERE symbol=? AND interval=? AND source IS NULL",
                (verdict, symbol, interval),
            ).rowcount
        print(f"stamped {written} rows")


if __name__ == "__main__":
    main()
