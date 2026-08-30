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

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from prometheus_client import make_asgi_app
from pydantic import BaseModel, Field

from .. import (bus, chat, config, db, intervals, scoring, security, sources,
                symbols)
from ..llm import (LLMAuthFailed, LLMKeyRequired, LLMUpstreamError, get_llm)
from ..features import build_feature_packet
from ..orchestrator import analyze
from ..paper import summarize
from ..metrics import SSE_CLIENTS
from . import scorer

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
        # pipeline progress, so stage 1 and stage 2 move independently
        await nc.subscribe("snapshot.built.>", cb=_fanout),
        await nc.subscribe("analysis.stage1.completed.>", cb=_fanout),
        # replay progress, so the UI can show a run later
        await nc.subscribe("replay.progress.>", cb=_fanout),
    ]
    print(f"[api] bus connected {config.NATS_URL}")
    print(f"[api] sources: {', '.join(sources.names())}")
    yield
    for sub in subs:
        await sub.unsubscribe()
    await nc.drain()


app = FastAPI(title="candle-agent", lifespan=lifespan)

# The terminal runs on its own origin in dev (vite on :5174) and from a
# static host in production, so the browser needs explicit permission to
# call this service at all - without it every fetch and the SSE stream
# fail before they reach a route.
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    # X-LLM-Key must be listed or the browser will not send it cross-origin
    allow_headers=["Content-Type", "X-LLM-Key"],
)

# by IP, whoever's key is in play: a visitor key protects their wallet,
# not this server's CPU
_limiter = security.RateLimiter(config.RATE_LIMIT_PER_HOUR)


def _enforce_limit(request: Request):
    ip = security.client_ip(request, config.TRUST_PROXY_HEADERS)
    allowed, retry_after = _limiter.check(ip)
    if not allowed:
        raise HTTPException(
            429,
            f"Rate limit reached ({config.RATE_LIMIT_PER_HOUR} requests/hour). "
            f"Try again in {retry_after}s.",
            headers={"Retry-After": str(retry_after)},
        )


def _check_key_transport(request: Request, key: str | None):
    """A visitor key may not cross plain HTTP.

    Loopback is exempt so local development works; anything else needs TLS
    or an explicit ALLOW_INSECURE_KEY_HEADER opt-in.
    """
    if not key:
        return
    if security.is_secure(request) or config.ALLOW_INSECURE_KEY_HEADER:
        return
    if security.is_loopback(request):
        return
    raise HTTPException(
        400,
        "An API key may only be sent over HTTPS. Refusing to accept "
        "X-LLM-Key on an insecure connection.",
    )

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
def latest(symbol: str, interval: str | None = None):
    """Latest analysis, optionally scoped to one interval.

    The terminal scopes it: an analysis of the 1m series says nothing about
    the 5m one, so restoring across intervals would show the wrong levels.
    """
    a = db.latest_analysis(symbol.upper(), interval)
    if not a:
        raise HTTPException(404, "no analysis yet - POST /api/analyze/{symbol}")
    return a


@app.post("/api/llm/test")
async def test_key(request: Request, x_llm_key: str | None = Header(default=None)):
    """Smallest possible upstream call, to tell a good key from a bad one.

    Nothing is stored and nothing is analysed; the key exists only for the
    duration of this call.
    """
    _enforce_limit(request)
    _check_key_transport(request, x_llm_key)

    try:
        llm = get_llm(api_key=x_llm_key)
    except LLMKeyRequired as e:
        raise HTTPException(400, str(e)) from None

    try:
        # ping() carries no response_format, so it cannot trip the
        # "messages must contain the word json" rule
        await asyncio.to_thread(llm.ping)
    except LLMAuthFailed as e:
        # already scrubbed at the client, scrubbed again on the way out
        return {"valid": False, "model": llm.model,
                "detail": security.scrub(e, x_llm_key)}
    except Exception as e:                              # noqa: BLE001
        raise HTTPException(502, security.scrub(
            f"Could not reach the LLM provider: {e}", x_llm_key)) from None

    return {"valid": True, "model": llm.model, "detail": "Key accepted."}


@app.post("/api/analyze/{symbol}")
async def trigger(request: Request, symbol: str,
                  x_llm_key: str | None = Header(default=None)):
    """Run or queue a two-stage analysis.

    With a visitor key the analysis runs INLINE, in this process, and the
    result comes straight back. It deliberately does not go through the
    bus: JetStream persists messages to disk, so putting a key on a
    request subject would write it down - exactly what must never happen.

    Without a key the old path is unchanged: publish a request, the
    analyzer picks it up, the result arrives over SSE (202).
    """
    _enforce_limit(request)
    _check_key_transport(request, x_llm_key)
    symbol = symbol.upper()

    if not x_llm_key:
        if config.LLM_PROVIDER == "mock" or config.LLM_API_KEY:
            await bus.publish(_state["js"], bus.ANALYSIS_REQUEST.format(symbol=symbol),
                              {"symbol": symbol, "ts": 0})
            return JSONResponse({"status": "queued", "symbol": symbol}, status_code=202)
        raise HTTPException(
            400,
            "An LLM API key is required. This server has none configured, so "
            "supply your own in Settings - it is used for this request only "
            "and never stored.")

    try:
        llm = get_llm(api_key=x_llm_key)
    except LLMKeyRequired as e:
        raise HTTPException(400, str(e)) from None

    loop = asyncio.get_running_loop()

    def emit(name: str, payload: dict):
        template = bus.PROGRESS_SUBJECTS.get(name)
        if template is not None:
            asyncio.run_coroutine_threadsafe(
                bus.publish_core(_state["nc"], template.format(symbol=symbol), payload),
                loop)

    try:
        # blocking HTTP inside; keep the event loop free
        result = await asyncio.to_thread(analyze, symbol, config.MIN_BARS, llm, emit)
    except LLMAuthFailed as e:
        raise HTTPException(400, security.scrub(e, x_llm_key)) from None
    except LLMUpstreamError as e:
        raise HTTPException(502, security.scrub(e, x_llm_key)) from None
    except RuntimeError as e:
        # not enough bars, or validation failed after retries
        raise HTTPException(422, security.scrub(e, x_llm_key)) from None

    # the RESULT carries no key, so it is safe to fan out like any other
    await bus.publish(_state["js"], bus.ANALYSIS_COMPLETED.format(symbol=symbol),
                      {"symbol": symbol, "bar_ts": 0, **result})
    return {"status": "completed", "symbol": symbol, "key_source": "user", **result}


class ChatTurn(BaseModel):
    role: str = Field(pattern="^(user|agent|assistant)$")
    text: str = Field(max_length=chat.MAX_MESSAGE_CHARS)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=chat.MAX_MESSAGE_CHARS)
    interval: str | None = None
    # the client sends what it is showing; the server trims it anyway
    history: list[ChatTurn] = Field(default_factory=list, max_length=40)


@app.post("/api/chat/{symbol}")
async def followup(request: Request, symbol: str, req: ChatRequest,
                   x_llm_key: str | None = Header(default=None)):
    """Answer a question about the stored analysis for this symbol.

    Runs INLINE and never touches the bus, for the same reason
    /api/analyze does not when a visitor key is present: JetStream
    persists to disk, so a keyed request published to a subject would
    write the key down. There is no queued fallback here either - a
    follow-up is a request/response exchange, and a 202 with no way to
    deliver the answer would be a worse lie than a 400.

    Nothing this returns is validated and nothing it returns is stored.
    It must not reach the analyses table: the scorer reads that table,
    and an unvalidated answer in it would pollute the population.
    """
    _enforce_limit(request)
    _check_key_transport(request, x_llm_key)
    symbol = symbol.upper()

    analysis = db.latest_analysis(symbol, req.interval)
    if analysis is None:
        raise HTTPException(
            404,
            f"No stored analysis for {symbol}. Run an analysis first - a "
            "follow-up explains a verdict that already exists.")

    bars = db.recent_bars(symbol, limit=100, interval=analysis.get("interval"))
    if not bars:
        raise HTTPException(
            404, f"No stored bars for {symbol}, so there is no table to cite.")

    if not x_llm_key and config.LLM_PROVIDER != "mock" and not config.LLM_API_KEY:
        raise HTTPException(
            400,
            "An LLM API key is required. This server has none configured, so "
            "supply your own in Settings - it is used for this request only "
            "and never stored.")

    try:
        llm = get_llm(api_key=x_llm_key)
    except LLMKeyRequired as e:
        raise HTTPException(400, str(e)) from None

    messages = chat.build_messages(
        symbol, analysis, build_feature_packet(bars), req.history, req.message)

    try:
        # blocking HTTP inside; keep the event loop free
        reply = await asyncio.to_thread(llm.converse, messages)
    except LLMAuthFailed as e:
        raise HTTPException(400, security.scrub(e, x_llm_key)) from None
    except LLMUpstreamError as e:
        raise HTTPException(502, security.scrub(e, x_llm_key)) from None

    usage = [u for u in getattr(llm, "usage", []) if u]
    return {
        "symbol": symbol,
        "reply": reply,
        "model": llm.model,
        "analysis_ts": analysis.get("ts"),
        "key_source": "user" if x_llm_key else "server",
        # measured, not estimated - the same rule the analysis path follows
        "prompt_tokens": sum(u.get("prompt_tokens") or 0 for u in usage) or None,
        "completion_tokens": sum(u.get("completion_tokens") or 0 for u in usage) or None,
    }


class ReplayRequest(BaseModel):
    symbol: str = Field(min_length=1)
    interval: str = config.INTERVAL
    start: int = 0
    end: int = 2 ** 62

    max_analyses: int | None = None
    # every Nth bar; 1 = every bar, which is the pre-stride behaviour
    stride: int = Field(default=1, ge=1)
    dry_run: bool = False


@app.post("/api/replay")
async def start_replay(request: Request, req: ReplayRequest):
    """Start a replay run, or price one first with dry_run=true.

    max_analyses has no default on purpose: every bar costs an LLM call,
    so a run cannot begin without the caller naming a ceiling.

    stride analyses every Nth bar. It defaults to 1, so existing callers
    are unaffected; raising it buys forward windows that do not overlap,
    which is what makes a run's results worth more than its row count.
    """
    _enforce_limit(request)
    if req.max_analyses is None:
        raise HTTPException(
            400,
            "max_analyses is required: a replay costs one LLM call per bar. "
            "Send dry_run=true first to see the estimated cost.")

    try:
        reply = await _state["nc"].request(
            bus.REPLAY_CONTROL_START, req.model_dump_json().encode(), timeout=30)
    except (asyncio.TimeoutError, TimeoutError):
        raise HTTPException(503, "replay service did not respond") from None

    result = json.loads(reply.data)
    if result.get("status") != "ok":
        raise HTTPException(400, result.get("message", "replay refused the request"))
    return result


@app.get("/api/replay/{run_id}")
def replay_progress(run_id: int):
    run = db.get_replay_run(run_id)
    if not run:
        raise HTTPException(404, f"no replay run {run_id}")
    return run


@app.post("/api/replay/{run_id}/stop")
async def stop_replay(run_id: int):
    try:
        reply = await _state["nc"].request(
            bus.REPLAY_CONTROL_STOP,
            json.dumps({"run_id": run_id}).encode(), timeout=10)
    except (asyncio.TimeoutError, TimeoutError):
        raise HTTPException(503, "replay service did not respond") from None

    result = json.loads(reply.data)
    if result.get("status") != "ok":
        raise HTTPException(404, result.get("message", "could not stop that run"))
    return result


class ScoreRequest(BaseModel):
    """Scoring is free - no LLM call - so unlike a replay it needs no cap."""

    symbol: str = Field(min_length=1)
    interval: str = config.INTERVAL
    # one replay run, or several pooled into a single sample
    replay_run_id: int | list[int] | None = None
    start: int | None = None
    end: int | None = None
    # merged over scoring.DEFAULTS; an unknown key is an error, never
    # silently dropped, or the scores would disagree with the parameters
    # recorded next to them
    params: dict = Field(default_factory=dict)


@app.post("/api/score")
async def start_score(request: Request, req: ScoreRequest):
    """Score stored analyses against what happened next.

    On demand only, and never coupled to replay finishing: every
    threshold in the scoring layer is a judgement call, so re-running
    with different parameters is the normal case rather than the
    exception. Each run is a new row - old scores are not stale, they are
    answers to a different question.
    """
    _enforce_limit(request)
    try:
        run = await asyncio.to_thread(
            scorer.run, req.symbol, req.interval, req.replay_run_id,
            req.params, req.start, req.end)
    except scoring.ScoringError as e:
        raise HTTPException(400, str(e)) from None
    return run


@app.get("/api/score/{score_run_id}")
def score_detail(score_run_id: int):
    run = db.get_score_run(score_run_id)
    if not run:
        raise HTTPException(404, f"no score run {score_run_id}")
    return {**run, "scores": db.get_scores(score_run_id)}


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
