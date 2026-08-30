"""Bar-geometry and indicator features computed locally (never by the LLM)."""


def ema(values, period):
    if not values:
        return []
    k = 2 / (period + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def atr(bars, period=14):
    """Average True Range over dict bars with high/low/close."""
    if len(bars) < 2:
        return [0.0] * len(bars)
    trs = [bars[0]["high"] - bars[0]["low"]]
    for prev, cur in zip(bars, bars[1:]):
        tr = max(
            cur["high"] - cur["low"],
            abs(cur["high"] - prev["close"]),
            abs(cur["low"] - prev["close"]),
        )
        trs.append(tr)
    return ema(trs, period)


def bar_geometry(bars):
    """Per-bar descriptors: body ratio, direction, inside/outside bar,
    breakout follow-through (Al Brooks style)."""
    out = []
    for i, b in enumerate(bars):
        rng = b["high"] - b["low"]
        body = abs(b["close"] - b["open"])
        feat = {
            "ts": b["ts"],
            "direction": "bull" if b["close"] >= b["open"] else "bear",
            "body_ratio": round(body / rng, 2) if rng > 0 else 0.0,
            "inside_bar": False,
            "outside_bar": False,
            "breakout_follow": False,
        }
        if i > 0:
            p = bars[i - 1]
            feat["inside_bar"] = b["high"] <= p["high"] and b["low"] >= p["low"]
            feat["outside_bar"] = b["high"] > p["high"] and b["low"] < p["low"]
            broke_up = b["close"] > p["high"]
            broke_dn = b["close"] < p["low"]
            feat["breakout_follow"] = (broke_up and feat["direction"] == "bull") or (
                broke_dn and feat["direction"] == "bear"
            )
        out.append(feat)
    return out


def nearest_level_distance(price: float, atr: float, levels) -> float | None:
    """How far a price sits from the nearest diagnosed key level, in ATR.

    Lives here rather than in scoring.py because the validator needs it
    too, and the import must not run that way: scoring writes only to
    score tables, and nothing it produces may ever reach a prompt. Pure
    geometry over current data belongs with the other features, where
    both sides can import it without inverting that rule.
    """
    if not levels or not atr or atr <= 0:
        return None
    numeric = [float(x) for x in levels if isinstance(x, (int, float))]
    if not numeric:
        return None
    return round(min(abs(price - x) for x in numeric) / atr, 4)


def envelope_atr(bars, atr_value) -> float | None:
    """High-to-low range of a bar window, in ATR units.

    The backward half of the cycle grader's amplitude ratio. It is
    computed HERE, over exactly the bars the model was shown, and stored
    on the analysis row - the scorer may not recompute it, because
    scoring reads only bars strictly after the analysis and a
    backward-looking read there could silently drift from the window the
    verdict was actually formed against.
    """
    if not bars or not atr_value or atr_value <= 0:
        return None
    return round((max(b["high"] for b in bars)
                  - min(b["low"] for b in bars)) / atr_value, 4)


def build_feature_packet(bars, n_recent=30):
    """Everything stage 1 needs, as a compact text table. K1 = latest closed bar."""
    closes = [b["close"] for b in bars]
    ema20 = ema(closes, 20)
    atr14 = atr(bars, 14)
    geo = bar_geometry(bars)
    recent = bars[-n_recent:]
    recent_geo = geo[-n_recent:]
    lines = ["idx,open,high,low,close,volume,dir,body_ratio,inside,outside,brk_follow"]
    for j, (b, g) in enumerate(zip(recent, recent_geo)):
        k = len(recent) - j  # K1 = newest
        lines.append(
            f"K{k},{b['open']},{b['high']},{b['low']},{b['close']},{b['volume']},"
            f"{g['direction']},{g['body_ratio']},{int(g['inside_bar'])},"
            f"{int(g['outside_bar'])},{int(g['breakout_follow'])}"
        )
    return {
        "bar_table": "\n".join(lines),
        # the amplitude a cycle claim is made against, over exactly
        # the window the table shows
        "envelope_atr": envelope_atr(recent, round(atr14[-1], 2)),
        "ema20": round(ema20[-1], 2),
        "atr14": round(atr14[-1], 2),
        "last_close": closes[-1],
        "price_vs_ema": "above" if closes[-1] >= ema20[-1] else "below",
    }
