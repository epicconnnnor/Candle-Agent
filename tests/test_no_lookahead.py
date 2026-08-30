"""No future bar may reach an analysis.

This is a LIVE correctness property, not just a replay one. Before the
as-of bound existed, an analysis of bar N called db.recent_bars() with no
upper bound and got whatever was newest in the table. On the live path
that is a later bar whenever the analyzer lagged ingest or JetStream
redelivered a message - so the analysis attributed to bar N could have
been formed on bars N+1, N+2, ...

Every test here is written against the live path. Replay inherits the
guarantee rather than having its own.
"""
import json
import os
import tempfile

import pytest

os.environ["DB_PATH"] = os.path.join(tempfile.gettempdir(), "test_no_lookahead.db")

from candle_agent import db
from candle_agent.features import build_feature_packet
from candle_agent.orchestrator import analyze
from candle_agent.services import analyzer as analyzer_svc

from .fake_source import ramp

SYMBOL = "LOOKAHEAD"
STEP_MS = 60_000
START_TS = 1_700_000_000_000


@pytest.fixture(autouse=True)
def series(monkeypatch, tmp_path):
    """200 bars. Bars 0..99 are the 'past'; 100..199 must never be visible
    to an analysis of bar 99."""
    monkeypatch.setenv("DB_PATH", str(tmp_path / "lookahead.db"))
    bars = ramp(200, start=100.0, step=0.5, step_ms=STEP_MS, start_ts=START_TS)
    db.insert_bars(SYMBOL, "1m", bars)
    return bars


def ts_of(index: int) -> int:
    return START_TS + index * STEP_MS


class RecordingLLM:
    """Captures the exact prompt text each stage was shown."""

    model = "recording"

    def __init__(self):
        self.usage: list[dict] = []
        self.prompts: list[str] = []

    def complete(self, system, user):
        self.prompts.append(user)
        if "STAGE-1" in system:
            return json.dumps({"regime": "range", "cycle": "compression",
                               "strength": "weak",
                               "key_levels": [100.0], "summary": "flat"})
        return json.dumps({"decision": "no_trade", "entry": None, "stop": None,
                           "target": None, "risk_reward": None,
                           "confidence": "low", "reasoning_chain": ["none"],
                           "decision_path": [
                    {"node": "trend_alignment", "answer": "na", "because": "no trade"},
                    {"node": "level_proximity", "answer": "mid_range", "because": "no trade"},
                    {"node": "stop_placement", "answer": "na", "because": "no trade"},
                    {"node": "risk_reward", "answer": "na", "because": "no trade"}]})


# --- the storage layer --------------------------------------------------

def test_recent_bars_never_returns_a_bar_after_the_bound(series):
    cutoff = ts_of(99)
    got = db.recent_bars(SYMBOL, limit=500, interval="1m", as_of_ts=cutoff)

    assert got, "expected bars at or before the cutoff"
    assert max(b["ts"] for b in got) == cutoff
    assert all(b["ts"] <= cutoff for b in got), "a future bar leaked through"


def test_without_a_bound_the_newest_bar_is_returned(series):
    """Documents the unbounded behaviour the live path relied on, and why
    it was wrong to rely on it."""
    got = db.recent_bars(SYMBOL, limit=500, interval="1m")
    assert max(b["ts"] for b in got) == ts_of(199)


# --- the feature packet -------------------------------------------------

def test_feature_packet_contains_no_bar_after_the_bound(series):
    cutoff = ts_of(99)
    bars = db.recent_bars(SYMBOL, limit=100, interval="1m", as_of_ts=cutoff)
    packet = build_feature_packet(bars)

    expected_close = series[99]["close"]
    assert packet["last_close"] == expected_close

    # every future close, by value, must be absent from the rendered packet
    rendered = json.dumps(packet)
    future_closes = {b["close"] for b in series[100:]}
    past_closes = {b["close"] for b in series[:100]}
    leaked = sorted(c for c in future_closes - past_closes if str(c) in rendered)
    assert not leaked, f"future closes leaked into the packet: {leaked[:5]}"


# --- the live analysis path ---------------------------------------------

def test_live_analysis_of_bar_n_sees_only_bars_up_to_n(series):
    """The regression this exists for: analyzing bar 99 while bars 100-199
    are already stored, exactly what happens when the analyzer lags."""
    cutoff = ts_of(99)
    llm = RecordingLLM()
    analyze(SYMBOL, min_bars=30, llm=llm, as_of_ts=cutoff)

    shown = "\n".join(llm.prompts)
    future_closes = {b["close"] for b in series[100:]} - {b["close"] for b in series[:100]}
    leaked = sorted(c for c in future_closes if str(c) in shown)
    assert not leaked, f"the model was shown future prices: {leaked[:5]}"

    stored = db.latest_analysis(SYMBOL, "1m")
    assert stored["ts"] == cutoff, "analysis recorded against the wrong bar"
    assert stored["price_at"] == series[99]["close"]


def test_an_unbounded_analysis_does_see_the_future(series):
    """Proves the test above is not vacuous: drop the bound and future
    prices really do reach the model."""
    llm = RecordingLLM()
    analyze(SYMBOL, min_bars=30, llm=llm, as_of_ts=None)

    shown = "\n".join(llm.prompts)
    assert str(series[199]["close"]) in shown
    assert db.latest_analysis(SYMBOL, "1m")["ts"] == ts_of(199)


def test_analyzer_passes_the_message_timestamp_as_the_bound(monkeypatch, series):
    """The one-line analyzer change: whatever bar the message names is the
    bound. This is what makes replay and live behave identically."""
    seen = {}

    def fake_analyze(symbol, min_bars, llm, on_event, as_of_ts):
        seen["symbol"] = symbol
        seen["as_of_ts"] = as_of_ts
        return {"stage1": {"regime": "range"}, "stage2": {"decision": "no_trade"},
                "model": "x", "latency_ms": 1}

    monkeypatch.setattr(analyzer_svc, "analyze", fake_analyze)

    import asyncio

    class Msg:
        data = json.dumps({"symbol": SYMBOL, "ts": ts_of(99), "open": 1, "high": 1,
                           "low": 1, "close": 1, "volume": 1}).encode()
        metadata = None

        async def ack(self):
            pass

        async def nak(self, delay=0):
            pass

        async def term(self):
            pass

    class FakeJS:
        async def publish(self, subject, payload):
            pass

    asyncio.run(analyzer_svc._handle(FakeJS(), Msg(), forced=False))
    assert seen["as_of_ts"] == ts_of(99)


def test_manual_request_with_ts_zero_is_unbounded(monkeypatch, series):
    """POST /api/analyze publishes ts=0 meaning 'analyze now' - that must
    not become a bound of 0, which would select no bars at all."""
    seen = {}

    def fake_analyze(symbol, min_bars, llm, on_event, as_of_ts):
        seen["as_of_ts"] = as_of_ts
        return {"stage1": {"regime": "range"}, "stage2": {"decision": "no_trade"},
                "model": "x", "latency_ms": 1}

    monkeypatch.setattr(analyzer_svc, "analyze", fake_analyze)

    import asyncio

    class Msg:
        data = json.dumps({"symbol": SYMBOL, "ts": 0}).encode()
        metadata = None

        async def ack(self):
            pass

    class FakeJS:
        async def publish(self, subject, payload):
            pass

    asyncio.run(analyzer_svc._handle(FakeJS(), Msg(), forced=True))
    assert seen["as_of_ts"] is None
