"""Backoff pacing, Alpaca host routing, market hours, and the stall watchdog.

These cover the defects the live Alpaca run exposed:
  - a reconnect counter keyed on "socket opened" never grows, so a venue
    that accepts a subscription and then errors gets hammered
  - Alpaca stream code 400 was treated as retryable, so a junk symbol
    looped instead of raising
  - assets/clock live on the trading host, bars on the data host
  - "connected but silent" must be told apart from "market closed"
"""
import asyncio
import os
import tempfile

import pytest

os.environ["DB_PATH"] = os.path.join(tempfile.gettempdir(), "test_candle_agent_hours.db")

from candle_agent import config, db, sources
from candle_agent.services import ingest
from candle_agent.sources.alpaca import AlpacaSource
from candle_agent.sources.base import Backoff, UnknownSymbol
from candle_agent.sources.binance import BinanceSource

from .fake_source import FakeSource, ramp

SYMBOL = "FAKEUSDT"


# --- backoff -----------------------------------------------------------

def test_backoff_does_not_reset_on_a_retryable_close():
    """The bug: attempt reset every time the socket opened, so a venue that
    accepts then immediately errors was retried in a hot loop forever."""
    b = Backoff()
    for _ in range(4):
        b.fail()
    assert b.attempt == 4                   # nothing reset it


def test_backoff_only_resets_when_a_bar_actually_arrives():
    b = Backoff()
    b.fail()
    b.fail()
    assert b.attempt == 2
    b.progress()                            # a real bar
    assert b.attempt == 0


def test_backoff_ceiling_grows_and_is_capped():
    b = Backoff(base_s=1.0, cap_s=8.0)
    ceilings = []
    for _ in range(6):
        b.fail()
        ceilings.append(min(b.cap_s, b.base_s * 2 ** b.attempt))
    assert ceilings == [2.0, 4.0, 8.0, 8.0, 8.0, 8.0]


def test_backoff_delay_is_jittered_within_the_ceiling():
    b = Backoff(base_s=1.0, cap_s=4.0)
    delays = [Backoff(base_s=1.0, cap_s=4.0).fail() for _ in range(40)]
    assert all(0 <= d <= 4.0 for d in delays)
    assert len(set(delays)) > 1             # not a fixed delay
    del b


def test_backoff_escalates_every_nth_failure():
    b = Backoff(alert_every=3)
    alerts = []
    for _ in range(7):
        b.fail()
        alerts.append(b.should_alert())
    assert alerts == [False, False, True, False, False, True, False]


# --- alpaca error classification ---------------------------------------

def test_alpaca_code_400_is_fatal_not_retryable():
    """Regression: a junk symbol returns code 400 and used to be retried."""
    src = AlpacaSource("key", "secret")
    err = src._stream_error({"T": "error", "code": 400, "msg": "invalid syntax"})
    assert isinstance(err, UnknownSymbol)
    assert err.retryable is False
    assert "invalid syntax" in err.message


@pytest.mark.parametrize("code", [405, 408, 409, 410])
def test_alpaca_unfixable_codes_are_not_retried(code):
    src = AlpacaSource("key", "secret")
    assert src._stream_error({"T": "error", "code": code, "msg": "x"}).retryable is False


def test_alpaca_unknown_code_stays_retryable():
    src = AlpacaSource("key", "secret")
    assert src._stream_error({"T": "error", "code": 999, "msg": "x"}).retryable is True


def test_alpaca_already_authenticated_is_not_an_auth_failure():
    src = AlpacaSource("key", "secret")
    # 403 means "already authenticated" - benign, must not be fatal
    assert src._stream_error({"T": "error", "code": 403, "msg": "already"}).retryable


# --- alpaca host routing -----------------------------------------------

def _capture_get(src):
    calls = []

    async def fake_get(url, params, what):
        calls.append((url, params, what))
        if "clock" in url:
            return {"is_open": False, "next_open": "2026-08-28T13:30:00Z",
                    "next_close": None}
        return {"bars": [{"t": "2024-01-02T15:04:00Z", "o": 1, "h": 2,
                          "l": 0.5, "c": 1.5, "v": 100}]}

    src._get = fake_get
    return calls


def test_bars_go_to_the_data_host():
    src = AlpacaSource("key", "secret")
    calls = _capture_get(src)
    asyncio.run(src.history("AAPL", "5m", limit=200))

    url, params, _ = calls[0]
    assert url.startswith(config.ALPACA_DATA_URL)
    assert "/v2/stocks/AAPL/bars" in url
    assert params["timeframe"] == "5Min"        # mapped, not passed through
    assert params["limit"] == 200


def test_clock_goes_to_the_trading_host():
    src = AlpacaSource("key", "secret")
    calls = _capture_get(src)
    status = asyncio.run(src.market_status("AAPL"))

    assert calls[0][0].startswith(config.ALPACA_BASE_URL)
    assert calls[0][0].endswith("/v2/clock")
    assert status["is_open"] is False
    assert status["next_open"] == "2026-08-28T13:30:00Z"
    assert status["known"] is True


def test_no_host_ever_carries_a_doubled_version_path():
    src = AlpacaSource("key", "secret")
    calls = _capture_get(src)
    asyncio.run(src.history("AAPL", "1m"))
    asyncio.run(src.market_status("AAPL"))
    assert all("/v2/v2/" not in url for url, _, _ in calls)


def test_crypto_bars_use_the_crypto_endpoint():
    src = AlpacaSource("key", "secret")
    calls = _capture_get(src)
    asyncio.run(src.history("BTC/USD", "1m"))
    assert "/v1beta3/crypto/us/bars" in calls[0][0]


def test_crypto_is_always_open_without_calling_the_clock():
    src = AlpacaSource("key", "secret")
    calls = _capture_get(src)
    status = asyncio.run(src.market_status("BTC/USD"))
    assert status == {"is_open": True, "next_open": None,
                      "next_close": None, "known": True}
    assert calls == []                      # no clock request for crypto


def test_history_is_returned_oldest_first():
    src = AlpacaSource("key", "secret")

    async def fake_get(url, params, what):
        return {"bars": [
            {"t": "2024-01-02T15:06:00Z", "o": 3, "h": 3, "l": 3, "c": 3, "v": 1},
            {"t": "2024-01-02T15:04:00Z", "o": 1, "h": 1, "l": 1, "c": 1, "v": 1},
        ]}

    src._get = fake_get
    bars = asyncio.run(src.history("AAPL", "1m"))
    assert [b["ts"] for b in bars] == sorted(b["ts"] for b in bars)


def test_binance_history_parses_kline_rows():
    src = BinanceSource()

    class _R:
        status_code = 200

        @staticmethod
        def json():
            return [[1700000000000, "1.0", "2.0", "0.5", "1.5", "9.0", 1700000059999]]

    class _Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, params=None):
            assert "/api/v3/klines" in url
            assert params["interval"] == "4h"
            return _R()

    import candle_agent.sources.binance as mod
    original = mod.httpx.AsyncClient
    mod.httpx.AsyncClient = lambda *a, **k: _Client()
    try:
        bars = asyncio.run(src.history("BTCUSDT", "4h"))
    finally:
        mod.httpx.AsyncClient = original

    assert bars == [{"ts": 1700000000000, "open": 1.0, "high": 2.0,
                     "low": 0.5, "close": 1.5, "volume": 9.0}]


# --- ingest: backfill, market hours, watchdog --------------------------

@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.setattr(ingest.config, "INGEST_MODE", "live")
    for suffix in ("", "-wal", "-shm"):
        path = os.environ["DB_PATH"] + suffix
        if os.path.exists(path):
            os.remove(path)
    ingest._current = None
    ingest._task = None
    ingest._watchdog_task = None
    ingest._status.clear()
    ingest._last_bar_at = None
    ingest._stall_reported = False
    yield
    sources.reset(None)


class Rec:
    def __init__(self):
        self.pub = []

    async def publish(self, subject, payload):
        import json
        self.pub.append((subject, json.loads(payload.decode())))

    def states(self):
        return [p.get("state") for _, p in self.pub]

    def by_state(self, state):
        return [p for _, p in self.pub if p.get("state") == state]


def install_bus(monkeypatch):
    js, nc = Rec(), Rec()
    monkeypatch.setattr(ingest, "_js", js)
    monkeypatch.setattr(ingest, "_nc", nc)
    return js, nc


def test_backfill_completes_before_switch_returns(monkeypatch):
    """/subscribe must be able to read real bars straight after the reply."""
    install_bus(monkeypatch)
    src = FakeSource(history_bars=ramp(200), symbol=SYMBOL, hang=True)
    sources.reset({"fake": src})

    async def go():
        await ingest.switch("fake", SYMBOL, "5m")
        # deliberately no sleep: the bars must already be there
        stored = db.recent_bars(SYMBOL, limit=500, interval="5m")
        await ingest._stop()
        return stored

    stored = asyncio.run(go())
    assert len(stored) == 200
    assert src.history_calls == [(SYMBOL, "5m", 200)]


def test_market_closed_publishes_a_status_with_next_open(monkeypatch):
    _, nc = install_bus(monkeypatch)
    sources.reset({"fake": FakeSource(
        history_bars=ramp(10), symbol=SYMBOL, hang=True,
        market_open=False, next_open="2026-08-28T13:30:00Z")})

    async def go():
        await ingest.switch("fake", SYMBOL, "1m")
        await ingest._stop()

    asyncio.run(go())

    closed = nc.by_state("market_closed")
    assert closed, f"expected a market_closed event, got {nc.states()}"
    assert closed[0]["next_open"] == "2026-08-28T13:30:00Z"
    assert "closed" in closed[0]["message"]


def test_open_market_publishes_no_market_closed_event(monkeypatch):
    _, nc = install_bus(monkeypatch)
    sources.reset({"fake": FakeSource(history_bars=ramp(5), symbol=SYMBOL,
                                      hang=True, market_open=True)})

    async def go():
        await ingest.switch("fake", SYMBOL, "1m")
        await ingest._stop()

    asyncio.run(go())
    assert nc.by_state("market_closed") == []


def test_watchdog_fires_when_quiet_while_the_market_is_open(monkeypatch):
    _, nc = install_bus(monkeypatch)
    monkeypatch.setattr(ingest, "MIN_WATCHDOG_PERIOD_S", 0.05)
    monkeypatch.setattr(ingest, "STALL_FACTOR", 0)      # stale immediately
    sources.reset({"fake": FakeSource(stream_bars=ramp(1), symbol=SYMBOL,
                                      hang=True, market_open=True)})

    async def go():
        await ingest.switch("fake", SYMBOL, "1m")
        await asyncio.sleep(0.3)
        await ingest._stop()

    asyncio.run(go())

    stalled = nc.by_state("stalled")
    assert stalled, f"expected a stalled event, got {nc.states()}"
    assert stalled[0]["kind"] == "stalled"
    assert "market is open" in stalled[0]["message"]


def test_watchdog_stays_quiet_when_the_market_is_closed(monkeypatch):
    """A closed market is not a stall - this is the distinction that must
    not be guessed from the absence of bars."""
    _, nc = install_bus(monkeypatch)
    monkeypatch.setattr(ingest, "MIN_WATCHDOG_PERIOD_S", 0.05)
    monkeypatch.setattr(ingest, "STALL_FACTOR", 0)
    sources.reset({"fake": FakeSource(stream_bars=ramp(1), symbol=SYMBOL,
                                      hang=True, market_open=False)})

    async def go():
        await ingest.switch("fake", SYMBOL, "1m")
        await asyncio.sleep(0.3)
        await ingest._stop()

    asyncio.run(go())
    assert nc.by_state("stalled") == []


def test_backfill_failure_does_not_stop_the_stream(monkeypatch):
    _, nc = install_bus(monkeypatch)
    src = FakeSource(stream_bars=ramp(3), symbol=SYMBOL)

    async def broken_history(symbol, interval, limit=200):
        raise UnknownSymbol("no history for you")

    src.history = broken_history
    sources.reset({"fake": src})

    async def go():
        await ingest.switch("fake", SYMBOL, "1m")
        await asyncio.sleep(0.25)

    asyncio.run(go())

    assert nc.by_state("backfill_failed"), nc.states()
    assert len(db.recent_bars(SYMBOL, limit=50, interval="1m")) == 3   # stream ran


def test_full_backfill_reports_the_count(monkeypatch):
    _, nc = install_bus(monkeypatch)
    sources.reset({"fake": FakeSource(history_bars=ramp(200), symbol=SYMBOL, hang=True)})

    async def go():
        await ingest.switch("fake", SYMBOL, "1m")
        await ingest._stop()

    asyncio.run(go())
    done = nc.by_state("backfilled")
    assert done and done[0]["bars"] == 200
    assert done[0]["partial"] is False


def test_short_backfill_is_flagged_not_silent(monkeypatch):
    """Free data plans cap intraday history; a stubby chart must say why."""
    _, nc = install_bus(monkeypatch)
    sources.reset({"fake": FakeSource(history_bars=ramp(60), symbol=SYMBOL, hang=True)})

    async def go():
        await ingest.switch("fake", SYMBOL, "4h")
        await ingest._stop()

    asyncio.run(go())
    done = nc.by_state("backfilled")
    assert done and done[0]["partial"] is True
    assert done[0]["bars"] == 60 and done[0]["requested"] == 200
    assert "all the history it has" in done[0]["message"]
