"""Analyzer service.

Durable JetStream consumer in queue group "analyzers": scale it out with
more replicas and NATS load-balances bars across them. Each message is
ACKed only after the analysis is stored — kill an analyzer mid-run and
the bar is redelivered to a surviving replica (at-least-once delivery).

    python -m candle_agent.services.analyzer
"""
import asyncio
import time

from nats.js.api import AckPolicy, ConsumerConfig, DeliverPolicy

from .. import bus, config
from ..metrics import (ANALYSES, ANALYSIS_FAILURES, BUS_REDELIVERIES,
                       LLM_LATENCY, serve_metrics)
from ..orchestrator import analyze

_nc = None                        # core connection, for progress events
_bar_counts: dict[str, int] = {}   # per-symbol bars seen (for ANALYZE_EVERY)
_last_analyzed: dict[str, int] = {}  # per-symbol last analyzed bar ts (idempotency)


def _should_analyze(symbol: str, ts: int, forced: bool) -> bool:
    if _last_analyzed.get(symbol) == ts:      # duplicate delivery -> skip
        return False
    if forced:
        return True
    n = _bar_counts.get(symbol, 0) + 1
    _bar_counts[symbol] = n
    return n % config.ANALYZE_EVERY == 0


def _progress_emitter(loop, symbol: str):
    """A sync callback usable from the worker thread analyze() runs in.

    run_coroutine_threadsafe is the bridge: analyze() is blocking and lives
    off-loop, but the publish has to happen on it.
    """
    def emit(name: str, payload: dict):
        template = bus.PROGRESS_SUBJECTS.get(name)
        if template is None or _nc is None:
            return
        asyncio.run_coroutine_threadsafe(
            bus.publish_core(_nc, template.format(symbol=symbol), payload), loop)
    return emit


async def _handle(js, msg, forced: bool):
    data = bus.decode(msg)
    symbol, ts = data["symbol"], data.get("ts", 0)

    meta = msg.metadata
    if meta and meta.num_delivered > 1:
        BUS_REDELIVERIES.inc()
        print(f"[analyzer] redelivery #{meta.num_delivered} for {symbol} ts={ts}")

    if not _should_analyze(symbol, ts, forced):
        await msg.ack()
        return

    try:
        t0 = time.time()
        # analyze() is sync (blocking LLM HTTP calls) -> run in a thread so
        # the event loop keeps servicing heartbeats and other messages.
        emit = _progress_emitter(asyncio.get_running_loop(), symbol)
        # Pass the message's bar timestamp as a no-lookahead bound. The
        # analyzer cannot tell replay from live - it reads the same field
        # from the same payload either way - and on the live path this
        # closes a real gap: an analysis of bar N used to read whatever was
        # newest in the table, which is a LATER bar whenever this consumer
        # lagged ingest or JetStream redelivered. ts=0 (a manual
        # analysis.request) means unbounded, i.e. "analyze now".
        result = await asyncio.to_thread(
            analyze, symbol, config.MIN_BARS, None, emit, ts or None)
        LLM_LATENCY.observe(time.time() - t0)
        _last_analyzed[symbol] = ts
        ANALYSES.labels(symbol=symbol,
                        regime=result["stage1"]["regime"],
                        decision=result["stage2"]["decision"]).inc()
        await bus.publish(js, bus.ANALYSIS_COMPLETED.format(symbol=symbol),
                          {"symbol": symbol, "bar_ts": ts, **result})
        await msg.ack()
        print(f"[analyzer] {symbol} {result['stage1']['regime']} -> "
              f"{result['stage2']['decision']} ({result['latency_ms']} ms)")
    except Exception as e:
        ANALYSIS_FAILURES.inc()
        print(f"[analyzer] FAILED {symbol} ts={ts}: {e!r}")
        if meta and meta.num_delivered >= config.MAX_DELIVER:
            await msg.term()               # poison message: stop redelivering
        else:
            await msg.nak(delay=5)         # try again in 5s (maybe on a peer)


async def _consume(js, subject: str, durable: str, forced: bool):
    """Pull-consumer loop. Every analyzer replica binds to the SAME durable
    consumer, so JetStream hands each message to exactly one replica —
    horizontal scaling with no extra coordination."""
    cc = ConsumerConfig(ack_policy=AckPolicy.EXPLICIT,
                        deliver_policy=DeliverPolicy.NEW,
                        ack_wait=120)      # bar redelivered if no ack in 120s
    sub = await js.pull_subscribe(subject, durable=durable, config=cc)
    while True:
        try:
            msgs = await sub.fetch(1, timeout=5)
        except Exception:                  # fetch timeout: no messages, poll again
            continue
        for msg in msgs:
            await _handle(js, msg, forced)


async def main():
    global _nc

    serve_metrics()
    nc, js = await bus.connect()
    _nc = nc
    print(f"[analyzer] bus connected {config.NATS_URL}, analyze_every={config.ANALYZE_EVERY}")
    await asyncio.gather(
        _consume(js, "bars.closed.>", "analyzers-bars", forced=False),
        _consume(js, "analysis.request.>", "analyzers-req", forced=True),
    )


if __name__ == "__main__":
    asyncio.run(main())
