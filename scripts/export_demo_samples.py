"""Freeze a few real analyses into files the app ships with.

The stored demo has to be honest to be worth having: these are not
hand-written fixtures but analyses this system actually produced, exported
whole - the diagnosis, the decision, the checklist, and exactly the bars
the model was shown when it made them.

    python scripts/export_demo_samples.py            # dry run
    python scripts/export_demo_samples.py --apply

Re-run after a prompt change if the samples should show the current
contract; the fingerprint travels with each one so a stale sample is
identifiable rather than merely old.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from candle_agent import db  # noqa: E402
from candle_agent.services.api import DEMO_SAMPLES  # noqa: E402

# One per (symbol, interval): enough variety to show the app works on more
# than one instrument, few enough that they can all be eyeballed.
WANTED = [("AAPL", "1m"), ("MSFT", "1m"), ("TSLA", "1m")]

BARS = 200


def newest_full_analysis(symbol: str, interval: str):
    """The most recent analysis carrying the current contract's fields.

    A sample without `cycle` or `decision_path` would show a visitor a
    version of the product that no longer exists.
    """
    with db.conn() as c:
        rows = c.execute(
            "SELECT * FROM analyses WHERE symbol=? AND interval=? "
            "ORDER BY ts DESC LIMIT 50", (symbol, interval)).fetchall()
    for r in rows:
        stage1, stage2 = json.loads(r["stage1"]), json.loads(r["stage2"])
        if stage1.get("cycle") and stage2.get("decision_path"):
            return dict(r), stage1, stage2
    return None, None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    DEMO_SAMPLES.mkdir(parents=True, exist_ok=True)
    written = []

    for symbol, interval in WANTED:
        row, stage1, stage2 = newest_full_analysis(symbol, interval)
        if row is None:
            print(f"  {symbol} {interval}: no analysis with the current fields - skipped")
            continue

        # exactly the bars the analysis was allowed to see, by its own as-of
        # bound, so the chart a visitor sees is the chart the model read
        bars = db.recent_bars(symbol, limit=BARS, interval=interval,
                              as_of_ts=row["ts"])
        sample = {
            "id": f"{symbol.lower()}-{interval}",
            "symbol": symbol,
            "interval": interval,
            "bar_ts": row["ts"],
            "model": row["model"],
            "prompt_fingerprint": row["prompt_fingerprint"],
            "price_at": row["price_at"],
            "atr_at": row["atr_at"],
            "latency_ms": row["latency_ms"],
            "bars": [{k: b[k] for k in ("ts", "open", "high", "low", "close", "volume")}
                     for b in bars],
            "stage1": stage1,
            "stage2": stage2,
        }
        path = DEMO_SAMPLES / f"{sample['id']}.json"
        body = json.dumps(sample, indent=1)
        print(f"  {symbol} {interval}: {len(bars)} bars, {stage2['decision']}, "
              f"{len(body) / 1024:.0f} KB -> {path.name}")
        if args.apply:
            path.write_text(body, encoding="utf-8")
        written.append(path.name)

    print(f"\n{len(written)} samples"
          + ("" if args.apply else " (dry run - re-run with --apply)"))


if __name__ == "__main__":
    main()
