"""Paper-trading service.

Subscribes to analysis.completed.* (new signals) and bars.closed.*
(price updates). Simulates order fills, stops and targets against real
incoming bars and persists every trade — a forward test with zero
execution risk. Publishes paper.update.* events for the UI.

Crash-safe: the active trade lives in the DB, so a restarted replica
reloads it and carries on (state recovery, not just stateless retry).

    python -m market_agent.services.paper_trader
"""
import asyncio

from nats.js.api import AckPolicy, ConsumerConfig, DeliverPolicy

from .. import bus, config, db
from ..metrics import PAPER_OPEN, PAPER_TOTAL_R, PAPER_TRADES, serve_metrics
from ..paper import on_bar, summarize, trade_from_decision

_active: dict[str, dict] = {}   # symbol -> pending/open trade


def _load_state(symbol: str):
    t = db.active_trade(symbol)
    if t:
        _active[symbol] = t
        print(f"[paper] recovered {t['status']} trade #{t['id']} for {symbol}")


async def _publish_update(js, symbol: str):
    hist = db.trade_history(symbol)
    await bus.publish(js, bus.PAPER_UPDATE.format(symbol=symbol), {
        "symbol": symbol,
        "active": _active.get(symbol),
        "summary": summarize(hist, config.RISK_PER_TRADE),
    })


async def _on_signal(js, msg):
    data = bus.decode(msg)
    symbol = data["symbol"]
    if symbol not in _active and symbol != config.SYMBOL:
        _load_state(symbol)
    cur = _active.get(symbol)
    if cur and cur["status"] == "open":
        await msg.ack()                      # in a position: ignore new signals
        return
    trade = trade_from_decision(symbol, data.get("stage2", {}), data.get("bar_ts", 0))
    if trade:
        if cur and cur["status"] == "pending":   # newest analysis supersedes
            cur["status"] = "replaced"
            db.save_trade(cur)
        trade["id"] = db.save_trade(trade)
        _active[symbol] = trade
        print(f"[paper] new {trade['order_type']} {symbol} "
              f"entry={trade['entry']} stop={trade['stop']} target={trade['target']}")
        await _publish_update(js, symbol)
    await msg.ack()


async def _on_bar(js, msg):
    data = bus.decode(msg)
    symbol = data["symbol"]
    trade = _active.get(symbol)
    if not trade:
        await msg.ack()
        return
    before = trade["status"]
    on_bar(trade, data)
    if trade["status"] != before:
        db.save_trade(trade)
        print(f"[paper] {symbol} trade #{trade['id']}: {before} -> {trade['status']}"
              + (f" ({trade['exit_reason']}, {trade['r_multiple']}R)"
                 if trade["status"] == "closed" else ""))
        if trade["status"] in ("closed", "expired"):
            if trade["status"] == "closed":
                PAPER_TRADES.labels(symbol=symbol, exit_reason=trade["exit_reason"]).inc()
                PAPER_TOTAL_R.inc(trade["r_multiple"])
            del _active[symbol]
        PAPER_OPEN.set(sum(1 for t in _active.values() if t["status"] == "open"))
        await _publish_update(js, symbol)
    elif trade["status"] in ("pending", "open"):
        db.save_trade(trade)                 # persist bars_pending ticks
    await msg.ack()


async def _consume(js, subject, durable, handler):
    cc = ConsumerConfig(ack_policy=AckPolicy.EXPLICIT,
                        deliver_policy=DeliverPolicy.NEW, ack_wait=60)
    sub = await js.pull_subscribe(subject, durable=durable, config=cc)
    while True:
        try:
            msgs = await sub.fetch(1, timeout=5)
        except Exception:
            continue
        for m in msgs:
            await handler(js, m)


async def main():
    serve_metrics()
    _load_state(config.SYMBOL)
    nc, js = await bus.connect()
    print(f"[paper] bus connected {config.NATS_URL}, risk/trade=${config.RISK_PER_TRADE}")
    await asyncio.gather(
        _consume(js, "analysis.completed.>", "paper-signals", _on_signal),
        _consume(js, "bars.closed.>", "paper-bars", _on_bar),
    )


if __name__ == "__main__":
    asyncio.run(main())
