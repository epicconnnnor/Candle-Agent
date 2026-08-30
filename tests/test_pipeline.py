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


# --- progress events -----------------------------------------------------

def _nats_matches(subject: str, pattern: str) -> bool:
    """Minimal NATS subject matcher: '*' is one token, '>' is the rest."""
    s, p = subject.split("."), pattern.split(".")
    for i, tok in enumerate(p):
        if tok == ">":
            return len(s) > i
        if i >= len(s) or (tok != "*" and tok != s[i]):
            return False
    return len(s) == len(p)


def test_stage1_subject_does_not_collide_with_the_completed_wildcard():
    """Existing consumers bind to analysis.completed.> - the progress
    subject must not be swept up by it."""
    from candle_agent import bus
    stage1 = bus.STAGE1_COMPLETED.format(symbol="AAPL")
    assert not _nats_matches(stage1, "analysis.completed.>")
    assert _nats_matches(bus.ANALYSIS_COMPLETED.format(symbol="AAPL"), "analysis.completed.>")
    assert _nats_matches(stage1, "analysis.stage1.completed.>")


def test_progress_events_fire_in_order_and_before_stage_2(monkeypatch):
    """The whole point is timing: snapshot before stage 1, stage 1 before
    stage 2 is even asked for."""
    import json as _json
    from candle_agent.orchestrator import analyze

    db.insert_bars("PROGRESS", "1m", ramp(60))

    timeline: list[str] = []

    class ScriptedLLM:
        model = "scripted"
        usage: list[dict] = []

        def complete(self, system, user):
            stage = 1 if "STAGE-1" in system else 2
            timeline.append(f"llm:stage{stage}")
            if stage == 1:
                return _json.dumps({"regime": "range", "strength": "weak",
                                    "key_levels": [1.0], "summary": "flat"})
            return _json.dumps({"decision": "no_trade", "entry": None, "stop": None,
                                "target": None, "risk_reward": None,
                                "confidence": "low", "reasoning_chain": ["none"]})

    events: list[tuple[str, dict]] = []

    def on_event(name, payload):
        timeline.append(f"event:{name}")
        events.append((name, payload))

    monkeypatch.setenv("LLM_PROVIDER", "mock")
    analyze("PROGRESS", min_bars=30, llm=ScriptedLLM(), on_event=on_event)

    assert timeline == [
        "event:snapshot.built",
        "llm:stage1",
        "event:analysis.stage1.completed",
        "llm:stage2",
    ], timeline

    names = [n for n, _ in events]
    assert names == ["snapshot.built", "analysis.stage1.completed"]


def test_snapshot_payload_is_small_and_describes_the_window():
    from candle_agent.orchestrator import analyze
    from candle_agent.llm import MockLLM

    bars = ramp(60)
    db.insert_bars("SNAP", "1m", bars)
    events: list[tuple[str, dict]] = []
    analyze("SNAP", min_bars=30, llm=MockLLM(), on_event=lambda n, p: events.append((n, p)))

    payload = dict(events)["snapshot.built"]
    assert set(payload) == {"symbol", "interval", "bars", "first_ts", "last_ts"}
    assert payload["bars"] == 60
    assert payload["first_ts"] < payload["last_ts"]
    # nothing heavy: no bar table, no packet
    assert len(str(payload)) < 200


def test_stage1_event_carries_the_diagnosis():
    from candle_agent.orchestrator import analyze
    from candle_agent.llm import MockLLM

    db.insert_bars("DIAG", "1m", ramp(60))
    events: list[tuple[str, dict]] = []
    analyze("DIAG", min_bars=30, llm=MockLLM(), on_event=lambda n, p: events.append((n, p)))

    payload = dict(events)["analysis.stage1.completed"]
    assert payload["symbol"] == "DIAG"
    assert payload["stage1"]["regime"]
    assert "stage2" not in payload          # stage 2 has not happened yet


def test_analyze_still_works_without_an_event_callback():
    """on_event is optional; nothing may depend on a caller supplying it."""
    from candle_agent.orchestrator import analyze
    from candle_agent.llm import MockLLM

    db.insert_bars("NOCB", "1m", ramp(60))
    result = analyze("NOCB", min_bars=30, llm=MockLLM())
    assert result["stage1"]["regime"]


def test_delete_bars_is_scoped_to_one_symbol():
    db.insert_bars("KEEPME", "1m", ramp(5))
    db.insert_bars("DROPME", "1m", ramp(5))
    db.insert_bars("DROPME", "5m", ramp(5))

    removed = db.delete_bars("DROPME", "1m")

    assert removed == 5
    assert db.recent_bars("DROPME", interval="1m") == []
    assert len(db.recent_bars("DROPME", interval="5m")) == 5   # other interval kept
    assert len(db.recent_bars("KEEPME", interval="1m")) == 5   # other symbol kept


def test_delete_bars_refuses_an_empty_symbol():
    import pytest as _pytest
    with _pytest.raises(ValueError):
        db.delete_bars("")


def test_delete_bars_range_keeps_the_rest_of_the_series():
    db.insert_bars("SEGGY", "1m", ramp(10))
    stored = sorted(b["ts"] for b in db.recent_bars("SEGGY", limit=10, interval="1m"))

    removed = db.delete_bars_range("SEGGY", "1m", stored[3], stored[5])

    assert removed == 3
    left = sorted(b["ts"] for b in db.recent_bars("SEGGY", limit=10, interval="1m"))
    assert left == stored[:3] + stored[6:]      # window gone, both sides kept


def test_delete_bars_range_refuses_a_backwards_window():
    import pytest as _pytest
    with _pytest.raises(ValueError):
        db.delete_bars_range("SEGGY", "1m", 200, 100)


# --- bar provenance ----------------------------------------------------

def test_demo_bars_are_hidden_from_default_reads():
    db.insert_bars("PROVA", "1m", ramp(5), source="alpaca")
    db.insert_bars("PROVA", "1m", ramp(5, start_ts=1_700_000_000_000 + 5 * 60_000), source=db.SYNTHETIC)

    real = db.recent_bars("PROVA", limit=50, interval="1m")
    both = db.recent_bars("PROVA", limit=50, interval="1m", include_synthetic=True)

    assert len(real) == 5
    assert len(both) == 10


def test_legacy_rows_without_a_source_still_read_as_real():
    # NULL means "written before the column existed", not "synthetic" -
    # otherwise this migration would blank every existing database.
    db.insert_bars("PROVB", "1m", ramp(4))

    assert len(db.recent_bars("PROVB", limit=50, interval="1m")) == 4


def test_demo_mode_can_read_its_own_bars(monkeypatch):
    # The demo path writes a series it must be able to read back; the
    # filter is about keeping synthetic data out of *real* runs.
    db.insert_bars("PROVC", "1m", ramp(6), source=db.SYNTHETIC)
    assert db.recent_bars("PROVC", limit=50, interval="1m") == []

    monkeypatch.setenv("INGEST_MODE", "demo")
    assert len(db.recent_bars("PROVC", limit=50, interval="1m")) == 6


def test_a_demo_bar_cannot_choose_the_active_interval():
    db.insert_bars("PROVD", "5m", ramp(3), source="alpaca")
    db.insert_bars("PROVD", "1m", ramp(3, start_ts=1_700_000_000_000 + 60 * 60_000), source=db.SYNTHETIC)

    # the newest bar overall is synthetic, so the naive answer is "1m"
    assert db.active_interval("PROVD") == "5m"


def test_delete_bars_can_evict_by_provenance():
    db.insert_bars("PROVE", "1m", ramp(5), source="alpaca")
    db.insert_bars("PROVE", "1m", ramp(5, start_ts=1_700_000_000_000 + 5 * 60_000), source=db.SYNTHETIC)

    removed = db.delete_bars("PROVE", "1m", source=db.SYNTHETIC)

    assert removed == 5
    assert len(db.recent_bars("PROVE", limit=50, interval="1m",
                              include_synthetic=True)) == 5


def test_ingest_stamps_the_feed_that_produced_each_bar(monkeypatch):
    """Provenance comes from the source object, so it cannot be forgotten."""
    install_bus(monkeypatch)
    sources.reset({"fake": FakeSource(stream_bars=ramp(4), symbol="PROVF")})

    async def go():
        await ingest.switch("fake", "PROVF", "1m")
        await settle()

    asyncio.run(go())

    stored = db.recent_bars("PROVF", limit=100, interval="1m",
                            include_synthetic=True)
    assert stored, "the fake feed wrote nothing"
    assert {b["source"] for b in stored} == {"fake"}
