"""Replay service.

Feeds stored bars through the live pipeline one at a time. The analyzer
cannot tell replay from live: replay publishes on `bars.closed.<SYMBOL>`
with exactly the payload ingest publishes, and nothing in the message
says "replay".

The no-lookahead guarantee has two halves:

  * bar N+1 is not published until the analysis of bar N has come back on
    `analysis.completed.<SYMBOL>`. If the model is slower than the replay,
    the replay waits. There is no parallelism, by design.
  * the analysis of bar N may only read bars <= N. That is enforced in
    db.recent_bars via the as-of bound, which the analyzer takes from the
    message timestamp - the same field for replay and live.

Attribution is done here, not by the analyzer, precisely so the analyzer
stays ignorant: after each bar, rows written since the run started are
stamped with the run id.

    python -m candle_agent.services.replay
"""
import asyncio
import contextlib
import json
import os

from .. import bus, config, db
from ..metrics import serve_metrics

# a bar whose analysis never arrives; the model is dead or erroring
COMPLETION_TIMEOUT_S = float(os.environ.get("REPLAY_TIMEOUT_S", "180"))
# fallback when no measured usage exists yet (see db.token_stats)
ASSUMED_TOKENS_PER_ANALYSIS = 1800

_nc = None
_js = None
_waiters: dict[tuple[str, int], asyncio.Future] = {}
_tasks: dict[int, asyncio.Task] = {}


# --- completion tracking ------------------------------------------------

async def _on_completed(msg):
    """Resolve whoever is waiting on this bar."""
    data = bus.decode(msg)
    key = (data.get("symbol"), data.get("bar_ts"))
    fut = _waiters.get(key)
    if fut and not fut.done():
        fut.set_result(data)


async def _await_analysis(symbol: str, bar_ts: int, timeout: float):
    key = (symbol, bar_ts)
    fut = asyncio.get_running_loop().create_future()
    _waiters[key] = fut
    try:
        return await asyncio.wait_for(fut, timeout=timeout)
    except asyncio.TimeoutError:
        return None
    finally:
        _waiters.pop(key, None)


# --- validation ---------------------------------------------------------

async def _ingest_busy_with(symbol: str) -> bool:
    """True if live ingest is streaming this symbol.

    Replay and live must not share a symbol: attribution here works by
    stamping rows written during the run, and a live analysis landing in
    the middle would be stamped too.
    """
    try:
        reply = await _nc.request(bus.INGEST_CONTROL_STATUS, b"{}", timeout=3)
    except Exception:
        return False                # ingest not running at all
    state = json.loads(reply.data)
    current = state.get("current") or {}
    return bool(current.get("symbol") == symbol and state.get("streaming"))


def _validate(req: dict) -> tuple[dict | None, str | None]:
    symbol = (req.get("symbol") or "").upper()
    interval = req.get("interval") or config.INTERVAL
    max_analyses = req.get("max_analyses")

    if not symbol:
        return None, "symbol is required"
    if max_analyses is None:
        return None, ("max_analyses is required: a replay costs one LLM call "
                      "per bar and will not start without an explicit cap")
    try:
        max_analyses = int(max_analyses)
    except (TypeError, ValueError):
        return None, "max_analyses must be a whole number"
    if max_analyses <= 0:
        return None, "max_analyses must be greater than zero"

    if config.ANALYZE_EVERY != 1:
        return None, (f"replay requires ANALYZE_EVERY=1, found "
                      f"{config.ANALYZE_EVERY}: the analyzer would skip bars and "
                      "the replay would wait forever for analyses that never come")

    explicit_start = bool(req.get("start"))
    bars = db.bars_in_range(symbol, interval,
                            int(req.get("start") or 0),
                            int(req.get("end") or 2 ** 62))
    if not bars:
        return None, f"no stored {interval} bars for {symbol} in that range"

    warmup = db.count_bars_before(symbol, interval, bars[0]["ts"])
    if warmup < config.MIN_BARS:
        if explicit_start:
            # they asked for a specific window; do not silently move it
            return None, (f"only {warmup} bars exist before the window; the "
                          f"analyzer needs {config.MIN_BARS} of history to analyse "
                          "the first bar. Backfill further back, or start later")
        # no start given: spend the leading bars as warmup rather than failing
        needed = config.MIN_BARS - warmup
        if len(bars) <= needed:
            return None, (f"only {len(bars)} bars stored; at least "
                          f"{config.MIN_BARS + 1} are needed - {config.MIN_BARS} "
                          "for warmup and one to analyse")
        bars = bars[needed:]

    return {"symbol": symbol, "interval": interval, "bars": bars,
            "max_analyses": max_analyses, "warmup_used": warmup}, None


def estimate(bars_total: int, max_analyses: int, model: str | None) -> dict:
    """Cost a run before committing to it, from measured history if we have any."""
    planned = min(bars_total, max_analyses)
    stats = db.token_stats(model)
    if stats["samples"]:
        per = (stats["avg_prompt"] or 0) + (stats["avg_completion"] or 0)
        basis = f"measured from {stats['samples']} stored analyses"
    else:
        per = ASSUMED_TOKENS_PER_ANALYSIS
        basis = "estimated - no measured usage stored yet"
    return {
        "bars_total": bars_total,
        "analyses_planned": planned,
        "tokens_per_analysis": round(per),
        "estimated_tokens": round(per * planned),
        "basis": basis,
    }


# --- the run ------------------------------------------------------------

async def _run(run_id: int, symbol: str, interval: str, bars: list[dict],
               max_analyses: int):
    analysis_floor = db.max_id("analyses")
    trade_floor = db.max_id("paper_trades")
    db.update_replay_run(run_id, status="running")
    await _progress(run_id)

    done = analyses = 0
    status, detail = "completed", None

    for bar in bars:
        run = db.get_replay_run(run_id)
        if run and run["stop_requested"]:
            status, detail = "stopped", "stopped on request"
            break
        if analyses >= max_analyses:
            status, detail = "partial", f"reached the cap of {max_analyses} analyses"
            break

        payload = {"symbol": symbol, "ts": bar["ts"], "open": bar["open"],
                   "high": bar["high"], "low": bar["low"], "close": bar["close"],
                   "volume": bar["volume"]}
        # identical to what ingest publishes - nothing marks it as replay
        await bus.publish(_js, bus.BARS_CLOSED.format(symbol=symbol), payload)

        result = await _await_analysis(symbol, bar["ts"], COMPLETION_TIMEOUT_S)
        done += 1
        if result is None:
            status = "failed"
            detail = (f"no analysis returned for bar {bar['ts']} within "
                      f"{COMPLETION_TIMEOUT_S:.0f}s - aborting rather than "
                      "spending the budget on a broken run")
            break
        analyses += 1

        db.stamp_replay_rows(run_id, symbol, analysis_floor, trade_floor)
        db.update_replay_run(run_id, bars_done=done, analyses_done=analyses,
                             model=result.get("model"))
        await _progress(run_id)

    db.update_replay_run(run_id, status=status, bars_done=done,
                         analyses_done=analyses, detail=detail)
    db.stamp_replay_rows(run_id, symbol, analysis_floor, trade_floor)
    await _progress(run_id)
    _tasks.pop(run_id, None)


async def _progress(run_id: int):
    run = db.get_replay_run(run_id)
    if run and _nc is not None:
        await bus.publish_core(_nc, bus.REPLAY_PROGRESS.format(run_id=run_id), run)


# --- control ------------------------------------------------------------

async def _on_start(msg):
    req = json.loads(msg.data)
    parsed, error = _validate(req)
    if error:
        await msg.respond(json.dumps({"status": "error", "message": error}).encode())
        return

    bars = parsed["bars"]
    est = estimate(len(bars), parsed["max_analyses"], config.LLM_MODEL or None)

    if req.get("dry_run"):
        await msg.respond(json.dumps({"status": "ok", "dry_run": True, **est}).encode())
        return

    if await _ingest_busy_with(parsed["symbol"]):
        await msg.respond(json.dumps({
            "status": "error",
            "message": (f"live ingest is streaming {parsed['symbol']}; stop it "
                        "first - replay and live cannot share a symbol"),
        }).encode())
        return

    run_id = db.create_replay_run(
        symbol=parsed["symbol"], interval=parsed["interval"],
        start_ts=bars[0]["ts"], end_ts=bars[-1]["ts"], status="pending",
        bars_total=len(bars), max_analyses=parsed["max_analyses"],
        estimated_tokens=est["estimated_tokens"])

    _tasks[run_id] = asyncio.create_task(
        _run(run_id, parsed["symbol"], parsed["interval"], bars, parsed["max_analyses"]))

    await msg.respond(json.dumps({"status": "ok", "run_id": run_id, **est}).encode())


async def _on_stop(msg):
    req = json.loads(msg.data)
    run_id = int(req.get("run_id", 0))
    ok = db.request_replay_stop(run_id)
    await msg.respond(json.dumps({
        "status": "ok" if ok else "error",
        "run_id": run_id,
        "message": "stop requested" if ok else "no running replay with that id",
    }).encode())


async def main():
    global _nc, _js

    serve_metrics()
    _nc, _js = await bus.connect()
    print(f"[replay] bus connected {config.NATS_URL}")

    await _nc.subscribe(bus.REPLAY_CONTROL_START, cb=_on_start)
    await _nc.subscribe(bus.REPLAY_CONTROL_STOP, cb=_on_stop)
    await _nc.subscribe("analysis.completed.>", cb=_on_completed)

    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        for task in list(_tasks.values()):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await _nc.drain()


if __name__ == "__main__":
    asyncio.run(main())
