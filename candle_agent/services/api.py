"""API/UI service.

REST for bars + latest analysis, POST /api/analyze publishes an
analysis.request on the bus (event-driven â€?this service never runs the
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

from .. import bus, config, db
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
    sub1 = await nc.subscribe("analysis.completed.>", cb=_fanout)
    sub2 = await nc.subscribe("bars.closed.>", cb=_fanout)
    sub3 = await nc.subscribe("paper.update.>", cb=_fanout)
    print(f"[api] bus connected {config.NATS_URL}")
    yield
    await sub1.unsubscribe()
    await sub2.unsubscribe()
    await sub3.unsubscribe()
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
