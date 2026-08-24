"""Paper-trading fill simulation. Pure functions over dicts — no I/O —
so the exact same logic drives the live paper_trader service today and
the historical backtest harness later.

Trade lifecycle:  pending -> open -> closed(stop|target)
                  pending -> expired          (never filled)
                  pending -> replaced         (newer signal superseded it)

Conventions (documented because they decide the results):
- One trade per symbol at a time. New signals REPLACE a pending order
  (newest analysis wins) but are IGNORED while a position is open.
- Pessimistic same-bar rule: if one bar's range covers both stop and
  target, assume the STOP hit first. Without tick data you cannot know
  the true order, so score against yourself.
- Fills are frictionless (no slippage/fees) — noted limitation.
"""

LONG_TYPES = ("buy_limit", "buy_stop", "market_buy")
PENDING_TTL_BARS = 20   # unfilled order expires after this many bars


def trade_from_decision(symbol: str, decision: dict, bar_ts: int) -> dict | None:
    """Turn a stage-2 decision into a pending paper trade (or None)."""
    d = decision.get("decision")
    if d == "no_trade" or decision.get("entry") is None:
        return None
    return {
        "symbol": symbol,
        "direction": "long" if d in LONG_TYPES else "short",
        "order_type": d,
        "status": "pending",
        "entry": decision["entry"],
        "stop": decision["stop"],
        "target": decision["target"],
        "created_ts": bar_ts,
        "filled_ts": None, "closed_ts": None,
        "exit_price": None, "exit_reason": None,
        "r_multiple": None, "bars_pending": 0,
    }


def _fills(trade: dict, bar: dict) -> bool:
    """Would this pending order fill on this bar?"""
    t, e = trade["order_type"], trade["entry"]
    if t in ("market_buy", "market_sell"):
        trade["entry"] = bar["open"]          # market order: fill at open
        return True
    if t == "buy_limit":
        return bar["low"] <= e
    if t == "sell_limit":
        return bar["high"] >= e
    if t == "buy_stop":
        return bar["high"] >= e
    if t == "sell_stop":
        return bar["low"] <= e
    return False


def _close(trade: dict, price: float, reason: str, ts: int):
    trade["status"] = "closed"
    trade["exit_price"] = price
    trade["exit_reason"] = reason
    trade["closed_ts"] = ts
    risk = abs(trade["entry"] - trade["stop"]) or 1e-9
    move = (price - trade["entry"]) if trade["direction"] == "long" else (trade["entry"] - price)
    trade["r_multiple"] = round(move / risk, 3)


def on_bar(trade: dict, bar: dict) -> dict:
    """Advance one trade by one bar. Mutates and returns the trade."""
    if trade["status"] == "pending":
        if _fills(trade, bar):
            trade["status"] = "open"
            trade["filled_ts"] = bar["ts"]
            # a fill bar can also be an exit bar -> fall through
        else:
            trade["bars_pending"] += 1
            if trade["bars_pending"] >= PENDING_TTL_BARS:
                trade["status"] = "expired"
                trade["closed_ts"] = bar["ts"]
            return trade

    if trade["status"] == "open":
        is_long = trade["direction"] == "long"
        stop_hit = bar["low"] <= trade["stop"] if is_long else bar["high"] >= trade["stop"]
        target_hit = bar["high"] >= trade["target"] if is_long else bar["low"] <= trade["target"]
        if stop_hit:                      # pessimistic: stop wins ties
            _close(trade, trade["stop"], "stop", bar["ts"])
        elif target_hit:
            _close(trade, trade["target"], "target", bar["ts"])
    return trade


def summarize(closed_trades: list[dict], risk_per_trade: float = 100.0) -> dict:
    """Aggregate stats over closed (incl. expired) trades."""
    done = [t for t in closed_trades if t["status"] == "closed"]
    wins = [t for t in done if t["r_multiple"] > 0]
    total_r = round(sum(t["r_multiple"] for t in done), 3)
    return {
        "trades": len(done),
        "expired": sum(1 for t in closed_trades if t["status"] == "expired"),
        "wins": len(wins),
        "win_rate": round(len(wins) / len(done), 3) if done else None,
        "total_r": total_r,
        "avg_r": round(total_r / len(done), 3) if done else None,
        "pnl_usd": round(total_r * risk_per_trade, 2),   # fixed $ risk sizing
    }
