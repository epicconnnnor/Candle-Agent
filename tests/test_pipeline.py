"""End-to-end pipeline tests against a scripted DataSource.

Ingest, the analyzer's orchestrator, and the paper trader all run here
with no network, no NATS server and no exchange account - the bus is a
recording stub and the feed is tests.fake_source.FakeSource.
"""
import asyncio
import json
import os
import tempfile

import pytest

os.environ["LLM_PROVIDER"] = "mock"
os.environ["DB_PATH"] = os.path.join(tempfile.gettempdir(), "test_candle_agent_pipeline.db")

from candle_agent import db, paper, sources
from candle_agent.orchestrator import analyze
from candle_agent.services import ingest, paper_trader
from candle_agent.sources.base import RegionBlocked

from .fake_source import FakeSource, ramp

SYMBOL = "FAKEUSDT"


# --- bus stubs ---------------------------------------------------------

class FakeJS:
    """Stands in for JetStream: records what would have been published."""

    def __init__(self):
        self.published: list[tuple[str, dict]] = []

    async def publish(self, subject, payload):
        self.published.append((subject, json.loads(payload.decode())))

    def subjects(self):
        return [s for s, _ in self.published]

    def payloads(self, prefix):
        return [p for s, p in self.published if s.startswith(prefix)]


class FakeNC(FakeJS):
    """Core NATS publishes (status events) land here."""


class FakeMsg:
    def __init__(self, payload: dict):
        self.data = json.dumps(payload).encode()
        self.acked = False

    async def ack(self):
        self.acked = True


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    # these tests drive the registry, so ingest must not divert to the
    # synthetic demo source
    monkeypatch.setattr(ingest.config, "INGEST_MODE", "live")
    for suffix in ("", "-wal", "-shm"):     # WAL sidecars hold committed rows
        path = os.environ["DB_PATH"] + suffix
        if os.path.exists(path):
            os.remove(path)
    paper_trader._active.clear()
    ingest._current = None
    ingest._task = None
    ingest._status.clear()
    yield
    sources.reset(None)


def install_bus(monkeypatch):
    js, nc = FakeJS(), FakeNC()
    monkeypatch.setattr(ingest, "_js", js)
    monkeypatch.setattr(ingest, "_nc", nc)
    return js, nc


async def settle(seconds=0.25):
    """Let the feed task run to completion."""
    await asyncio.sleep(seconds)


# --- ingest ------------------------------------------------------------

def test_ingest_stores_bars_under_their_interval(monkeypatch):
    js, _ = install_bus(monkeypatch)
    src = FakeSource(stream_bars=ramp(5), symbol=SYMBOL)
    sources.reset({"fake": src})

    async def go():
        await ingest.switch("fake", SYMBOL, "5m")
        await settle()

    asyncio.run(go())

    stored = db.recent_bars(SYMBOL, limit=100, interval="5m")
    assert len(stored) == 5
    assert all(b["interval"] == "5m" for b in stored)
    # bars written under a different interval are a separate series
    assert db.recent_bars(SYMBOL, limit=100, interval="1m") == []


def test_bus_message_format_is_unchanged(monkeypatch):
    """The refactor must not alter what downstream consumers receive."""
    js, _ = install_bus(monkeypatch)
    sources.reset({"fake": FakeSource(stream_bars=ramp(2), symbol=SYMBOL)})

    async def go():
        await ingest.switch("fake", SYMBOL, "1m")
        await settle()

    asyncio.run(go())

    assert js.subjects() == [f"bars.closed.{SYMBOL}"] * 2
    payload = js.payloads("bars.closed")[0]
    assert set(payload) == {"symbol", "ts", "open", "high", "low", "close", "volume"}
    assert payload["symbol"] == SYMBOL


def test_history_is_stored_but_not_published(monkeypatch):
    js, _ = install_bus(monkeypatch)
    # the streamed bars continue past the history rather than overwriting it
    sources.reset({"fake": FakeSource(
        stream_bars=ramp(2, start=200.0, start_ts=1_700_000_000_000 + 30 * 60_000),
        history_bars=ramp(30), symbol=SYMBOL)})

    async def go():
        await ingest.switch("fake", SYMBOL, "1m")
        await settle()

    asyncio.run(go())

    assert len(db.recent_bars(SYMBOL, limit=100)) == 32   # 30 seeded + 2 streamed
    assert len(js.payloads("bars.closed")) == 2           # only the streamed ones


def test_subscribing_twice_to_the_same_feed_is_a_no_op(monkeypatch):
    install_bus(monkeypatch)
    src = FakeSource(stream_bars=ramp(2), symbol=SYMBOL, hang=True)
    sources.reset({"fake": src})

    async def go():
        first = await ingest.switch("fake", SYMBOL, "1m")
        await settle(0.05)
        second = await ingest.switch("fake", SYMBOL, "1m")
        await settle(0.05)
        await ingest._stop()
        return first, second

    first, second = asyncio.run(go())
    assert first is True and second is False
    assert src.streams_opened == 1        # not a second socket


def test_switching_interval_closes_the_previous_stream(monkeypatch):
    install_bus(monkeypatch)
    src = FakeSource(stream_bars=ramp(2), symbol=SYMBOL, hang=True)
    sources.reset({"fake": src})

    async def go():
        await ingest.switch("fake", SYMBOL, "1m")
        await settle(0.05)
        changed = await ingest.switch("fake", SYMBOL, "5m")
        await settle(0.05)
        await ingest._stop()
        return changed

    assert asyncio.run(go()) is True
    assert src.streams_opened == 2
    assert src.streams_closed == 2        # the old one was closed, not leaked
    assert src.open_calls == [(SYMBOL, "1m"), (SYMBOL, "5m")]


def test_unsupported_interval_is_rejected(monkeypatch):
    install_bus(monkeypatch)
    sources.reset({"fake": FakeSource(symbol=SYMBOL)})

    async def go():
        await ingest.switch("fake", SYMBOL, "7m")

    with pytest.raises(ValueError, match="7m"):
        asyncio.run(go())


# --- failure surfacing --------------------------------------------------

def test_region_block_is_published_as_status_not_swallowed(monkeypatch):
    _, nc = install_bus(monkeypatch)
    blocked = RegionBlocked("Binance refused the connection ... (HTTP 451).", code=451)
    sources.reset({"fake": FakeSource(fail_with=blocked, symbol=SYMBOL)})

    async def go():
        await ingest.switch("fake", SYMBOL, "1m")
        await settle()

    asyncio.run(go())

    statuses = nc.payloads("ingest.status")
    assert statuses, "a failed feed must publish a status event"
    failure = statuses[-1]
    assert failure["state"] == "failed"
    assert failure["kind"] == "region_blocked"
    assert failure["code"] == 451
    assert failure["retryable"] is False
    assert "451" in failure["message"]


def test_status_reaches_the_symbol_subject(monkeypatch):
    _, nc = install_bus(monkeypatch)
    sources.reset({"fake": FakeSource(stream_bars=ramp(1), symbol=SYMBOL)})

    async def go():
        await ingest.switch("fake", SYMBOL, "1m")
        await settle()

    asyncio.run(go())
    assert any(s == f"ingest.status.{SYMBOL}" for s in nc.subjects())


def test_unexpected_errors_are_reported_too(monkeypatch):
    _, nc = install_bus(monkeypatch)
    sources.reset({"fake": FakeSource(
        fail_with=ZeroDivisionError("boom"), symbol=SYMBOL)})

    async def go():
        await ingest.switch("fake", SYMBOL, "1m")
        await settle()

    asyncio.run(go())
    failure = nc.payloads("ingest.status")[-1]
    assert failure["state"] == "failed" and "boom" in failure["message"]


# --- analyzer ----------------------------------------------------------

def test_analyzer_runs_on_bars_from_the_fake_source(monkeypatch):
    install_bus(monkeypatch)
    sources.reset({"fake": FakeSource(history_bars=ramp(60), symbol=SYMBOL)})

    async def go():
        await ingest.switch("fake", SYMBOL, "5m")
        await settle()

    asyncio.run(go())

    result = analyze(SYMBOL)
    assert result["stage1"]["regime"] in ("bull_trend", "bear_trend", "range", "chop")
    assert result["stage2"]["decision"]

    stored = db.latest_analysis(SYMBOL)
    assert stored["interval"] == "5m"       # recorded against the live interval


# --- paper trader ------------------------------------------------------

def test_paper_trader_consumes_bars_from_the_fake_source(monkeypatch):
    js, _ = install_bus(monkeypatch)
    sources.reset({"fake": FakeSource(stream_bars=ramp(6), symbol=SYMBOL)})

    async def go():
        await ingest.switch("fake", SYMBOL, "1m")
        await settle()

        signal = {"symbol": SYMBOL, "bar_ts": 0, "stage2": {
            "decision": "buy_limit", "entry": 100.5, "stop": 99.5, "target": 102.5}}
        await paper_trader._on_signal(js, FakeMsg(signal))

        for _subject, bar in [(s, p) for s, p in js.published
                              if s.startswith("bars.closed")]:
            await paper_trader._on_bar(js, FakeMsg(bar))

    asyncio.run(go())

    updates = js.payloads("paper.update")
    assert updates, "the paper trader should have published an update"
    assert db.trade_history(SYMBOL) or db.active_trade(SYMBOL)


def test_crash_recovery_reloads_the_symbol_actually_traded():
    """Regression: recovery used config.SYMBOL, so it always found BTCUSDT."""
    trade = paper.trade_from_decision(
        "ETHUSDT",
        {"decision": "buy_limit", "entry": 10.0, "stop": 9.0, "target": 12.0},
        bar_ts=0)
    trade["id"] = db.save_trade(trade)

    paper_trader._active.clear()
    paper_trader._load_state()

    assert "ETHUSDT" in paper_trader._active
    assert paper_trader._active["ETHUSDT"]["id"] == trade["id"]


def test_crash_recovery_reloads_every_open_symbol():
    for symbol in ("ETHUSDT", "SOLUSDT"):
        t = paper.trade_from_decision(
            symbol,
            {"decision": "buy_limit", "entry": 10.0, "stop": 9.0, "target": 12.0},
            bar_ts=0)
        t["id"] = db.save_trade(t)

    paper_trader._active.clear()
    paper_trader._load_state()

    assert set(paper_trader._active) == {"ETHUSDT", "SOLUSDT"}
