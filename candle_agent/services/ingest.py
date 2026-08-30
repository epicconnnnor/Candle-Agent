"""Ingest service.

Streams one (source, symbol, interval) at a time and publishes each
CLOSED bar on the bus. The venue-specific parts live in candle_agent
.sources; this module is the supervisor around them:

    - it owns the feed task, so a subscribe request can cancel the current
      stream (closing its websocket) and start a new one,
    - it is idempotent: subscribing to what is already running is a no-op,
      never a second socket,
    - it publishes what the connection is doing on ingest.status.<SYMBOL>,
      so a failure shows up in the UI instead of looking like a hang.

Demo mode swaps in a synthetic source and takes the same code path.

    python -m candle_agent.services.ingest
"""
import asyncio
import contextlib
import json
import time

from .. import bus, config, db, sources
from ..intervals import to_ms
from ..metrics import BARS_INGESTED, serve_metrics
from ..sources.base import SourceError
from ..sources.demo import DemoSource

HISTORY_LIMIT = 200
STALL_FACTOR = 2                    # stalled once 2x the interval passes
MIN_WATCHDOG_PERIOD_S = 15.0

_nc = None
_js = None
_task: asyncio.Task | None = None
_watchdog_task: asyncio.Task | None = None
_current: dict | None = None        # {"source", "symbol", "interval"}
_status: dict = {}                  # last status event, replayed on subscribe
_lock = asyncio.Lock()
_demo_source: DemoSource | None = None
_last_bar_at: float | None = None   # monotonic clock of the last delivered bar
_stall_reported = False


# --- status ------------------------------------------------------------

def _emit_status(event: dict) -> dict:
    """Record the latest connection state and publish it.

    Called from source callbacks (sync) inside the running loop, so the
    publish is scheduled rather than awaited.
    """
    status = {"ts": int(time.time() * 1000), "mode": config.INGEST_MODE, **event}
    _status.clear()
    _status.update(status)

    detail = status.get("message") or status.get("reason") or ""
    print(f"[ingest] {status.get('state', 'status')}"
          f" {status.get('symbol', '')} {status.get('interval', '')}"
          f"{': ' + detail if detail else ''}")

    if _nc is not None:
        symbol = status.get("symbol") or config.SYMBOL
        with contextlib.suppress(RuntimeError):     # no running loop (tests)
            asyncio.get_running_loop().create_task(
                bus.publish_core(_nc, bus.INGEST_STATUS.format(symbol=symbol), status))
    return status


def current_status() -> dict:
    return dict(_status)


# --- feed --------------------------------------------------------------

async def _store_and_publish(symbol, interval, bar, source):
    global _last_bar_at, _stall_reported
    _last_bar_at = time.monotonic()
    if _stall_reported:                 # the feed came back
        _stall_reported = False
        _emit_status({"symbol": symbol, "interval": interval,
                      "state": "streaming",
                      "message": "bars are arriving again"})
    # `source` is the feed's own name, so demo mode stamps 'demo' without
    # anyone having to remember to: _resolve() hands back the demo source.
    db.insert_bar(symbol, interval, bar["ts"], bar["open"], bar["high"],
                  bar["low"], bar["close"], bar["volume"], source)
    # NOTE: the bus payload is unchanged from before intervals existed.
    # Downstream (analyzer, paper trader) is deliberately untouched.
    await bus.publish(_js, bus.BARS_CLOSED.format(symbol=symbol),
                      {"symbol": symbol, **bar})
    BARS_INGESTED.labels(symbol=symbol, mode=config.INGEST_MODE).inc()
    print(f"[ingest:{config.INGEST_MODE}] {symbol} {interval} "
          f"bar ts={bar['ts']} close={bar['close']}")


async def _backfill(source, symbol: str, interval: str) -> int:
    """Fetch real history before streaming starts.

    Awaited inside switch(), so by the time the api replies to /subscribe
    the bars are already stored and the chart can draw at once. A backfill
    failure is reported but does not stop the live stream.
    """
    try:
        bars = await source.history(symbol, interval, limit=HISTORY_LIMIT)
    except SourceError as e:
        _emit_status({"source": source.name, "symbol": symbol,
                      "interval": interval, "state": "backfill_failed",
                      **e.as_status()})
        return 0
    except Exception as e:                              # noqa: BLE001
        _emit_status({"source": source.name, "symbol": symbol,
                      "interval": interval, "state": "backfill_failed",
                      "kind": "error", "retryable": False,
                      "message": f"history fetch failed: {e!r}"})
        return 0

    if bars:
        db.insert_bars(symbol, interval, bars, source=source.name)   # stored, not republished
        print(f"[ingest] backfilled {len(bars)} {interval} bars for {symbol}")

    # A short backfill is normal - free data plans cap how far intraday
    # history goes back - but it must be visible, not inferred from a
    # chart that looks oddly stubby.
    partial = len(bars) < HISTORY_LIMIT
    _emit_status({
        "source": source.name, "symbol": symbol, "interval": interval,
        "state": "backfilled", "bars": len(bars), "requested": HISTORY_LIMIT,
        "partial": partial,
        "message": (f"{len(bars)} of {HISTORY_LIMIT} {interval} bars available"
                    " - the source returned all the history it has at this"
                    " interval" if partial else
                    f"loaded {len(bars)} {interval} bars of history"),
    })
    return len(bars)


async def _report_market_hours(source, symbol: str, interval: str) -> dict:
    """Publish whether the venue is open, so a closed market is legible.

    Without this, an equity subscribed at 02:00 looks identical to a
    broken feed: connected, authenticated, and silent.
    """
    try:
        status = await source.market_status(symbol)
    except Exception as e:                              # noqa: BLE001
        _emit_status({"source": source.name, "symbol": symbol,
                      "interval": interval, "state": "market_unknown",
                      "message": f"could not read market hours: {e!r}"})
        return {"is_open": True, "known": False}

    if status.get("known") and not status.get("is_open"):
        _emit_status({"source": source.name, "symbol": symbol,
                      "interval": interval, "state": "market_closed",
                      "next_open": status.get("next_open"),
                      "next_close": status.get("next_close"),
                      "message": ("the market is closed; showing history until "
                                  f"it reopens at {status.get('next_open')}")})
    return status


async def _watchdog(source, symbol: str, interval: str):
    """Fire when the feed goes quiet while the market is open.

    "Connected but silent" and "market closed" look identical from the
    socket, so the clock is what tells them apart - no guessing.
    """
    global _stall_reported

    interval_s = to_ms(interval) / 1000
    stall_after = STALL_FACTOR * interval_s
    # poll at half the interval, but never slower than half the stall
    # window, or a stall would be reported long after it began
    period = max(MIN_WATCHDOG_PERIOD_S, min(interval_s / 2, stall_after / 2))

    while True:
        await asyncio.sleep(period)
        if _last_bar_at is None or _stall_reported:
            continue
        quiet_for = time.monotonic() - _last_bar_at
        if quiet_for < stall_after:
            continue

        try:
            status = await source.market_status(symbol)
        except Exception:                               # noqa: BLE001
            continue                        # cannot tell; try again next tick
        if status.get("known") and not status.get("is_open"):
            continue                        # closed, not stalled

        _stall_reported = True
        _emit_status({"source": source.name, "symbol": symbol,
                      "interval": interval, "state": "stalled",
                      "kind": "stalled", "retryable": True,
                      "quiet_for_s": round(quiet_for),
                      "message": (f"no {interval} bar for {quiet_for:.0f}s "
                                  f"({STALL_FACTOR}x the interval) while the "
                                  "market is open")})


async def _feed(source, symbol: str, interval: str):
    """Stream until cancelled."""
    try:
        async for bar in source.stream(symbol, interval):
            await _store_and_publish(symbol, interval, bar, source.name)

        # a stream that returns instead of raising is still the feed going
        # quiet - say so rather than leaving a chart that stops updating
        _emit_status({"source": source.name, "symbol": symbol,
                      "interval": interval, "state": "ended",
                      "message": "the source stopped sending bars"})

    except asyncio.CancelledError:
        _emit_status({"source": source.name, "symbol": symbol,
                      "interval": interval, "state": "stopped",
                      "message": "unsubscribed"})
        raise
    except SourceError as e:
        # a fault the source already described: 451, bad symbol, auth
        _emit_status({"source": source.name, "symbol": symbol,
                      "interval": interval, "state": "failed", **e.as_status()})
    except Exception as e:                              # noqa: BLE001
        _emit_status({"source": source.name, "symbol": symbol,
                      "interval": interval, "state": "failed",
                      "kind": "error", "retryable": False,
                      "message": f"ingest stopped unexpectedly: {e!r}"})


def _resolve(source_name: str):
    """Demo mode ignores the registry; every venue is synthetic there."""
    global _demo_source
    if config.INGEST_MODE == "demo":
        if _demo_source is None:            # constructed lazily for tests
            _demo_source = DemoSource(on_event=_emit_status)
        return _demo_source
    return sources.get(source_name)


async def _stop():
    global _task, _watchdog_task
    for task in (_watchdog_task, _task):
        if task and not task.done():
            task.cancel()               # unwinds the source's `async with`,
            with contextlib.suppress(asyncio.CancelledError):
                await task              # which closes the websocket
    _task = _watchdog_task = None


async def switch(source_name: str, symbol: str, interval: str) -> bool:
    """Point ingest at a feed. Returns True if anything actually changed."""
    global _current, _task, _watchdog_task, _last_bar_at, _stall_reported

    async with _lock:
        desired = {"source": source_name, "symbol": symbol, "interval": interval}
        if _current == desired and _task and not _task.done():
            return False                # idempotent: already streaming this

        source = _resolve(source_name)
        supported = source.supported_intervals()
        if interval not in supported:
            raise ValueError(
                f"{source.name} does not support interval {interval!r}; "
                f"supported: {', '.join(supported)}")

        await _stop()
        _current = desired
        _last_bar_at = None
        _stall_reported = False

        # both awaited before returning, so the api's /subscribe reply
        # already reflects real history and the true market state
        await _backfill(source, symbol, interval)
        await _report_market_hours(source, symbol, interval)

        _task = asyncio.create_task(_feed(source, symbol, interval))
        _watchdog_task = asyncio.create_task(_watchdog(source, symbol, interval))
        return True


# --- control -----------------------------------------------------------

async def _on_control(msg):
    """Handle a subscribe request from the api service (request/reply)."""
    try:
        req = json.loads(msg.data)
        source_name = req.get("source") or config.DEFAULT_SOURCE
        symbol = req["symbol"]
        interval = req.get("interval") or config.INTERVAL
        changed = await switch(source_name, symbol, interval)
        reply = {"status": "ok", "changed": changed, "source": source_name,
                 "symbol": symbol, "interval": interval,
                 "mode": config.INGEST_MODE, "state": current_status()}
    except Exception as e:                              # noqa: BLE001
        reply = {"status": "error", "message": str(e)}
    await msg.respond(json.dumps(reply).encode())


async def _on_status_request(msg):
    """What is ingest streaming right now? Replay asks before starting."""
    await msg.respond(json.dumps({
        "current": _current,
        "streaming": bool(_task and not _task.done()),
        "mode": config.INGEST_MODE,
        "state": current_status(),
    }).encode())


async def main():
    global _nc, _js, _demo_source

    serve_metrics()
    _demo_source = DemoSource(on_event=_emit_status)
    # build the registry with the status hook wired in, so a source's
    # connection events reach the bus
    sources.get_registry(on_event=_emit_status)

    _nc, _js = await bus.connect()
    print(f"[ingest] bus connected {config.NATS_URL}, mode={config.INGEST_MODE}")

    await _nc.subscribe(bus.INGEST_CONTROL, cb=_on_control)
    await _nc.subscribe(bus.INGEST_CONTROL_STATUS, cb=_on_status_request)
    await switch(config.DEFAULT_SOURCE, config.SYMBOL, config.INTERVAL)

    try:
        while True:                     # the feed task does the work
            await asyncio.sleep(3600)
    finally:
        await _stop()
        await _nc.drain()


if __name__ == "__main__":
    asyncio.run(main())
