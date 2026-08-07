from market_agent.paper import (PENDING_TTL_BARS, on_bar, summarize,
                                trade_from_decision)


def bar(o, h, l, c, ts=1000):
    return {"ts": ts, "open": o, "high": h, "low": l, "close": c, "volume": 1.0}


def mk_trade(order_type="buy_limit", entry=100.0, stop=99.0, target=102.0):
    return trade_from_decision(
        "TEST",
        {"decision": order_type, "entry": entry, "stop": stop, "target": target},
        bar_ts=0,
    )


def test_no_trade_produces_no_trade():
    assert trade_from_decision("TEST", {"decision": "no_trade"}, 0) is None


def test_buy_limit_fills_when_low_touches_entry():
    t = mk_trade()
    on_bar(t, bar(101, 101.5, 100.5, 101))     # never touches 100
    assert t["status"] == "pending"
    on_bar(t, bar(101, 101.2, 99.9, 100.5))    # low pierces entry
    assert t["status"] == "open"


def test_stop_hit_gives_minus_one_r():
    t = mk_trade()
    on_bar(t, bar(100.5, 100.6, 100.0, 100.2))  # fill at 100
    on_bar(t, bar(100.2, 100.3, 98.8, 99.0))    # low through stop 99
    assert t["status"] == "closed" and t["exit_reason"] == "stop"
    assert t["r_multiple"] == -1.0


def test_target_hit_gives_two_r():
    t = mk_trade()                               # risk 1, reward 2
    on_bar(t, bar(100.5, 100.6, 100.0, 100.2))
    on_bar(t, bar(100.2, 102.5, 100.1, 102.4))   # high through target 102
    assert t["status"] == "closed" and t["exit_reason"] == "target"
    assert t["r_multiple"] == 2.0


def test_pessimistic_same_bar_rule_stop_wins():
    t = mk_trade()
    on_bar(t, bar(100.5, 100.6, 100.0, 100.2))
    on_bar(t, bar(100.2, 103.0, 98.5, 101.0))    # bar covers BOTH stop and target
    assert t["exit_reason"] == "stop"            # assume worst case


def test_fill_and_stop_on_same_bar():
    t = mk_trade()
    on_bar(t, bar(101, 101, 98.5, 99))           # fills at 100 AND breaks stop
    assert t["status"] == "closed" and t["exit_reason"] == "stop"


def test_pending_order_expires():
    t = mk_trade()
    for i in range(PENDING_TTL_BARS):
        on_bar(t, bar(105, 106, 104, 105, ts=1000 + i))  # price never comes back
    assert t["status"] == "expired"


def test_market_order_fills_at_open():
    t = mk_trade(order_type="market_buy", entry=100.0, stop=99.0, target=102.0)
    on_bar(t, bar(100.7, 101, 100.5, 100.9))
    assert t["status"] == "open" and t["entry"] == 100.7


def test_short_sell_limit_lifecycle():
    t = mk_trade(order_type="sell_limit", entry=100.0, stop=101.0, target=98.0)
    on_bar(t, bar(99.5, 100.2, 99.4, 99.8))      # high touches entry -> short open
    assert t["status"] == "open"
    on_bar(t, bar(99.8, 99.9, 97.9, 98.1))       # low through target
    assert t["exit_reason"] == "target" and t["r_multiple"] == 2.0


def test_summary_math():
    trades = []
    for r in (2.0, -1.0, 2.0):
        t = mk_trade()
        t["status"], t["r_multiple"] = "closed", r
        trades.append(t)
    e = mk_trade(); e["status"] = "expired"; trades.append(e)
    s = summarize(trades, risk_per_trade=100)
    assert s["trades"] == 3 and s["expired"] == 1
    assert s["wins"] == 2 and s["total_r"] == 3.0
    assert s["pnl_usd"] == 300.0
