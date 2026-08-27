"""API/UI service.

REST for bars + latest analysis, POST /api/analyze publishes an
analysis.request on the bus (event-driven — this service never runs the
LLM itself), and /api/events is an SSE stream: the service holds an
ephemeral bus subscription and fans events out to every connected browser.

    uvicorn candle_agent.services.api:app
"""
import asyncio
import json
import pathlib
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from prometheus_client import make_asgi_app
from pydantic import BaseModel, Field

from .. import bus, config, db, intervals, sources, symbols
from ..paper import summarize
from ..metrics import SSE_CLIENTS

STATIC = pathlib.Path(__file__).parent.parent / "static"
_clients: set[asyncio.Queue] = set()
_state = {}


async def _fanout(msg):
    event = {"subject": msg.subject, "data": bus.decode(msg)}
    for q in list(_clients):
        q.put_nowait(event)


@asynccontextmanager
async def lifespan(app):
    nc, js = await bus.connect()
    _state["nc"], _state["js"] = nc, js
    # ephemeral (non-durable) subscriptions: the browser only cares about
    # events that happen while it's watching.
    subs = [
        await nc.subscribe("analysis.completed.>", cb=_fanout),
        await nc.subscribe("bars.closed.>", cb=_fanout),
        await nc.subscribe("paper.update.>", cb=_fanout),
        # connection state from ingest: a 451 or a bad symbol has to reach
        # the browser, not just the ingest log
        await nc.subscribe("ingest.status.>", cb=_fanout),
    ]
    print(f"[api] bus connected {config.NATS_URL}")
    print(f"[api] sources: {', '.join(sources.names())}")
    yield
    for sub in subs:
        await sub.unsubscribe()
    await nc.drain()


app = FastAPI(title="candle-agent", lifespan=lifespan)
app.mount("/metrics", make_asgi_app())


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/healthz")
def healthz():
    """Liveness/readiness probe target for Kubernetes."""
    ok = _state.get("nc") is not None and _state["nc"].is_connected
    if not ok:
        raise HTTPException(503, "bus disconnected")
    return {"status": "ok"}


@app.get("/api/bars/{symbol}")
def bars(symbol: str, limit: int = 200):
    return db.recent_bars(symbol.upper(), limit=limit)


@app.get("/symbols")
async def list_symbols(source: str | None = None, asset_class: str | None = None):
    """Every tradable symbol, merged across registered sources.

    Cached for SYMBOLS_TTL_S. `unavailable` names any source that could
    not be reached, so a partial list is never mistaken for a complete one.
    """
    try:
        catalogue = await symbols.get_symbols()
    except symbols.SymbolsUnavailable as e:
        raise HTTPException(503, str(e))

    items = [s for s in catalogue
             if (source is None or s.source == source)
             and (asset_class is None or s.asset_class == asset_class)]
    return {
        "symbols": [s.as_dict() for s in items],
        "sources": sources.names(),
        "intervals": list(intervals.INTERVALS),
        "unavailable": symbols.last_errors(),
    }


class SubscribeRequest(BaseModel):
    symbol: str = Field(min_length=1)
    interval: str = config.INTERVAL
    source: str | None = None       # inferred from the symbol when omitted


@app.post("/subscribe")
async def subscribe(req: SubscribeRequest):
    """Point ingest at a feed and hand back enough bars to draw a chart."""
    symbol = req.symbol.strip().upper()
    interval = req.interval

    if not intervals.is_valid(interval):
        raise HTTPException(
            400, f"unsupported interval {interval!r}; supported: {intervals.SUPPORTED}")

    try:
        entry = await symbols.lookup(symbol)
    except symbols.SymbolsUnavailable as e:
        # cannot prove the symbol is bad when the catalogue is unreachable
        raise HTTPException(503, f"symbol list unavailable, cannot validate: {e}")

    if entry is None:
        raise HTTPException(400, f"unknown symbol {symbol!r}")

    source = req.source or entry.source
    if source not in sources.names():
        raise HTTPException(
            400, f"unknown source {source!r}; registered: {', '.join(sources.names())}")

    try:
        reply = await _state["nc"].request(
            bus.INGEST_CONTROL,
            json.dumps({"symbol": symbol, "interval": interval, "source": source}).encode(),
            timeout=10,
        )
    except (asyncio.TimeoutError, TimeoutError):
        raise HTTPException(503, "ingest service did not respond")

    result = json.loads(reply.data)
    if result.get("status") != "ok":
        raise HTTPException(400, result.get("message", "ingest rejected the request"))

    return {
        **result,
        "asset_class": entry.asset_class,
        "bars": db.recent_bars(symbol, limit=200, interval=interval),
    }


@app.get("/api/analysis/{symbol}")
def latest(symbol: str):
    a = db.latest_analysis(symbol.upper())
    if not a:
        raise HTTPException(404, "no analysis yet - POST /api/analyze/{symbol}")
    return a


@app.post("/api/analyze/{symbol}", status_code=202)
async def trigger(symbol: str):
    """Publish a request; the analyzer picks it up. 202 = accepted, result
    arrives asynchronously on the SSE stream."""
    symbol = symbol.upper()
    await bus.publish(_state["js"], bus.ANALYSIS_REQUEST.format(symbol=symbol),
                      {"symbol": symbol, "ts": 0})
    return {"status": "queued", "symbol": symbol}


@app.get("/api/paper/{symbol}")
def paper(symbol: str):
    """Active paper position, trade history, and aggregate stats."""
    symbol = symbol.upper()
    return {
        "active": db.active_trade(symbol),
        "history": db.trade_history(symbol, limit=20),
        "summary": summarize(db.trade_history(symbol, limit=1000), config.RISK_PER_TRADE),
    }


@app.get("/api/events")
async def events():
    q: asyncio.Queue = asyncio.Queue()
    _clients.add(q)
    SSE_CLIENTS.inc()

    async def stream():
        try:
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"data: {json.dumps(ev)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"   # comment frame keeps proxies happy
        finally:
            _clients.discard(q)
            SSE_CLIENTS.dec()

    return StreamingResponse(stream(), media_type="text/event-stream")
