from candle_agent.features import ema, atr, bar_geometry, build_feature_packet


def mk(o, h, l, c, ts=0, v=1.0):
    return {"ts": ts, "open": o, "high": h, "low": l, "close": c, "volume": v}


def test_ema_converges_to_constant():
    vals = [10.0] * 50
    assert abs(ema(vals, 20)[-1] - 10.0) < 1e-9


def test_atr_positive_and_length_matches():
    bars = [mk(10, 11, 9, 10.5, ts=i) for i in range(20)]
    a = atr(bars, 14)
    assert len(a) == 20 and all(x >= 0 for x in a)


def test_inside_and_outside_bar_flags():
    bars = [mk(10, 12, 8, 11, ts=0), mk(11, 11.5, 9, 10, ts=1), mk(10, 13, 7, 12, ts=2)]
    geo = bar_geometry(bars)
    assert geo[1]["inside_bar"] and not geo[1]["outside_bar"]
    assert geo[2]["outside_bar"] and not geo[2]["inside_bar"]


def test_breakout_follow_through():
    bars = [mk(10, 10.5, 9.5, 10.2, ts=0), mk(10.2, 11.2, 10.1, 11.0, ts=1)]
    geo = bar_geometry(bars)
    assert geo[1]["breakout_follow"]


def test_feature_packet_shape():
    bars = [mk(10 + i * 0.1, 10.2 + i * 0.1, 9.9 + i * 0.1, 10.1 + i * 0.1, ts=i) for i in range(40)]
    p = build_feature_packet(bars)
    assert p["price_vs_ema"] in ("above", "below")
    assert p["bar_table"].count("\n") == 30  # header + 30 rows
