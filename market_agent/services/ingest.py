"""Ingest service.

Live mode:  holds a persistent TLS websocket to Binance (<symbol>@kline_1m),
            stores each CLOSED bar and publishes it on the bus.
            Reconnects forever with exponential backoff + full jitter.
Demo mode:  seeds N historical synthetic bars, then emits a new synthetic
            bar every DEMO_INTERVAL_S so the whole pipeline runs offline.

    python -m market_agent.services.ingest
"""
import asyncio
import json
import random
import time

from .. import bus, config, db
from ..demo import next_bar, synthetic_bars
from ..metrics import BARS_INGESTED, INGEST_LAG, WS_RECONNECTS, serve_metrics

BACKOFF_BASE_S = 1.0
BACKOFF_CAP_S = 60.0


async def _store_and_publish(js, symbol, bar, mode):
    db.insert_bar(symbol, bar["ts"], bar["open"], bar["high"],
                  bar["low"], bar["close"], bar["volume"])
    await bus.publish(js, bus.BARS_CLOSED.format(symbol=symbol),
                      {"symbol": symbol, **bar})
    BARS_INGESTED.labels(symbol=symbol, mode=mode).inc()
    print(f"[ingest:{mode}] {symbol} bar ts={bar['ts']} close={bar['close']}")


async def run_live(js, symbol: str):
    import websockets  # lazy: demo mode needs no network deps

    url = config.BINANCE_WS.format(stream=f"{symbol.lower()}@kline_1m")
    attempt = 0
    while True:
        try:
            # ping_interval/ping_timeout: detect half-open TCP connections
            # (the peer vanished but no FIN/RST ever reached us).
            async with websockets.connect(url, ping_interval=20, ping_timeout=10) as ws:
                print(f"[ingest:live] connected {url}")
                attempt = 0
                async for raw in ws:
                    k = json.loads(raw).get("k", {})
                    if not k.get("x"):          # only CLOSED bars
                        continue
                    bar = {
                        "ts": k["t"],
                        "open": float(k["o"]), "high": float(k["h"]),
                        "low": float(k["l"]), "close": float(k["c"]),
                        "volume": float(k["v"]),
                    }
                    # one-way delay estimate: bar close time -> local arrival
                    INGEST_LAG.observe(max(0.0, time.time() - k["T"] / 1000))
                    await _store_and_publish(js, symbol, bar, "live")
        except Exception as e:
            attempt += 1
            WS_RECONNECTS.inc()
            # exponential backoff with FULL JITTER: avoids a thundering herd
            # of clients reconnecting in lockstep after an exchange outage.
            delay = random.uniform(0, min(BACKOFF_CAP_S, BACKOFF_BASE_S * 2 ** attempt))
            print(f"[ingest:live] connection lost ({e!r}); reconnect #{attempt} in {delay:.1f}s")
            await asyncio.sleep(delay)


async def run_demo(js, symbol: str):
    bars = synthetic_bars(config.DEMO_SEED_BARS)
    for b in bars:                              # seed history: DB only, no bus spam
        db.insert_bar(symbol, b["ts"], b["open"], b["high"], b["low"], b["close"], b["volume"])
    print(f"[ingest:demo] seeded {len(bars)} bars for {symbol}")

    rng = random.Random()
    last = bars[-1]
    while True:
        last = next_bar(last, rng)
        await _store_and_publish(js, symbol, last, "demo")
        await asyncio.sleep(config.DEMO_INTERVAL_S)


async def main():
    serve_metrics()
    nc, js = await bus.connect()
    print(f"[ingest] bus connected {config.NATS_URL}, mode={config.INGEST_MODE}")
    try:
        if config.INGEST_MODE == "live":
            await run_live(js, config.SYMBOL)
        else:
            await run_demo(js, config.SYMBOL)
    finally:
        await nc.drain()


if __name__ == "__main__":
    asyncio.run(main())
