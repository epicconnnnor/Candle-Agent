"""Synthetic random-walk bar generator (offline demo + tests).
Mild uptrend after the first third so stage 1 has structure to diagnose."""
import random
import time


def synthetic_bars(n_bars=120, seed=42, start_price=100.0, end_ts_ms=None,
                   step_ms=60_000):
    rng = random.Random(seed)
    price = start_price
    step_s = step_ms / 1000
    # align the newest bar to an interval boundary, as a real feed would
    end_ts_ms = end_ts_ms or int(time.time() // step_s * step_ms)
    ts = end_ts_ms - n_bars * step_ms
    out = []
    for i in range(n_bars):
        drift = 0.05 if i > n_bars // 3 else 0.0
        o = price
        c = max(1.0, o + rng.gauss(drift, 0.4))
        h = max(o, c) + abs(rng.gauss(0, 0.15))
        l = min(o, c) - abs(rng.gauss(0, 0.15))
        v = round(abs(rng.gauss(50, 15)), 1)
        out.append(
            {"ts": ts, "open": round(o, 2), "high": round(h, 2),
             "low": round(l, 2), "close": round(c, 2), "volume": v}
        )
        price = c
        ts += step_ms
    return out


def next_bar(prev: dict, rng: random.Random, step_ms=60_000):
    """Continue a synthetic series one bar past `prev`."""
    o = prev["close"]
    c = max(1.0, o + rng.gauss(0.03, 0.4))
    h = max(o, c) + abs(rng.gauss(0, 0.15))
    l = min(o, c) - abs(rng.gauss(0, 0.15))
    return {
        "ts": prev["ts"] + step_ms, "open": round(o, 2), "high": round(h, 2),
        "low": round(l, 2), "close": round(c, 2),
        "volume": round(abs(rng.gauss(50, 15)), 1),
    }
