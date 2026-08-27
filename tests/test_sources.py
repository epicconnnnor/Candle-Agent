"""Source-layer tests: the interface contract, Binance's parsing and
error classification, Alpaca's parsing, and the local aggregator.

Nothing here touches the network.
"""
import asyncio
import os
import tempfile

import pytest

os.environ.setdefault("DB_PATH", os.path.join(tempfile.gettempdir(), "test_candle_agent_sources.db"))

from candle_agent import sources
from candle_agent.sources import binance as binance_mod
from candle_agent.sources.alpaca import AlpacaSource, _to_ms
from candle_agent.sources.base import (CRYPTO, AuthFailed, DataSource,
                                       RegionBlocked, SourceUnavailable,
                                       StreamClosed, SymbolInfo, UnknownSymbol,
                                       aggregate)
from candle_agent.sources.binance import BinanceSource

from .fake_source import FakeSource, ramp


# --- interface ---------------------------------------------------------

@pytest.mark.parametrize("source", [
    BinanceSource(),
    AlpacaSource("key", "secret"),
    FakeSource(),
])
def test_sources_implement_the_interface(source):
    assert isinstance(source, DataSource)
    assert source.name
    assert source.supported_intervals()


def test_symbol_info_serializes_extra_fields():
    info = SymbolInfo(symbol="BTCUSDT", name="BTC/USDT", asset_class=CRYPTO,
                      source="binance", extra={"baseAsset": "BTC", "quoteAsset": "USDT"})
    assert info.as_dict() == {
        "symbol": "BTCUSDT", "name": "BTC/USDT", "asset_class": "crypto",
        "source": "binance", "baseAsset": "BTC", "quoteAsset": "USDT",
    }


def test_alpaca_requires_credentials():
    with pytest.raises(AuthFailed):
        AlpacaSource("", "")


# --- binance -----------------------------------------------------------

def test_binance_parses_only_closed_bars():
    src = BinanceSource()
    open_bar = '{"k": {"x": false, "t": 1, "T": 2, "o": "1", "h": "2", "l": "0.5", "c": "1.5", "v": "9"}}'
    assert src._parse(open_bar) is None

    closed = '{"k": {"x": true, "t": 1700000000000, "T": 1700000059999, "o": "1", "h": "2", "l": "0.5", "c": "1.5", "v": "9"}}'
    bar = src._parse(closed)
    assert bar == {"ts": 1700000000000, "open": 1.0, "high": 2.0,
                   "low": 0.5, "close": 1.5, "volume": 9.0}


def test_binance_surfaces_stream_errors():
    src = BinanceSource()
    with pytest.raises(UnknownSymbol):
        src._parse('{"error": {"code": -1121, "msg": "Invalid symbol."}}')


class _Resp:
    def __init__(self, status_code):
        self.status_code = status_code


class _Rejected(Exception):
    """Stand-in for websockets' handshake rejection."""

    def __init__(self, status):
        self.response = _Resp(status)


class _Closed(Exception):
    def __init__(self, code, reason):
        self.rcvd = type("F", (), {"code": code, "reason": reason})()


def test_451_classifies_as_region_blocked():
    err = binance_mod.classify(_Rejected(451))
    assert isinstance(err, RegionBlocked)
    assert err.retryable is False
    assert err.code == 451
    assert "region" in err.message.lower()
    assert err.as_status()["kind"] == "region_blocked"


def test_auth_rejection_is_not_retryable():
    assert isinstance(binance_mod.classify(_Rejected(403)), AuthFailed)


def test_other_http_status_is_retryable():
    err = binance_mod.classify(_Rejected(500))
    assert isinstance(err, SourceUnavailable) and err.retryable is True


def test_close_code_and_reason_are_reported():
    err = binance_mod.classify(_Closed(1006, "abnormal closure"))
    assert isinstance(err, StreamClosed)
    assert err.code == 1006
    assert "1006" in err.message and "abnormal closure" in err.message


def test_unclassifiable_failure_still_describes_itself():
    err = binance_mod.classify(OSError("dns went away"))
    assert isinstance(err, SourceUnavailable)
    assert "dns went away" in err.message


# --- alpaca ------------------------------------------------------------

def test_alpaca_parses_bar_messages():
    src = AlpacaSource("key", "secret")
    raw = ('[{"T":"b","S":"AAPL","o":1.0,"h":2.0,"l":0.5,"c":1.5,"v":100,'
           '"t":"2024-01-02T15:04:00Z"}]')
    bars = src._parse(raw, "AAPL")
    assert len(bars) == 1
    assert bars[0]["open"] == 1.0 and bars[0]["close"] == 1.5
    assert bars[0]["ts"] == _to_ms("2024-01-02T15:04:00Z")


def test_alpaca_ignores_other_symbols_and_control_frames():
    src = AlpacaSource("key", "secret")
    raw = '[{"T":"success","msg":"authenticated"},{"T":"b","S":"MSFT","o":1,"h":1,"l":1,"c":1,"v":1,"t":"2024-01-02T15:04:00Z"}]'
    assert src._parse(raw, "AAPL") == []


def test_alpaca_auth_error_is_classified():
    src = AlpacaSource("key", "secret")
    err = src._stream_error({"T": "error", "code": 402, "msg": "auth failed"})
    assert isinstance(err, AuthFailed) and err.retryable is False


def test_alpaca_never_puts_credentials_in_errors():
    src = AlpacaSource("SECRET-KEY-ID", "SUPER-SECRET")
    err = src._stream_error({"T": "error", "code": 402, "msg": "auth failed"})
    assert "SUPER-SECRET" not in str(err) and "SECRET-KEY-ID" not in str(err)


def test_alpaca_timestamp_handles_nanoseconds():
    assert _to_ms("2024-01-02T15:04:05.123456789Z") == _to_ms("2024-01-02T15:04:05.123456Z")


# --- aggregation -------------------------------------------------------

async def _ayield(bars):
    for b in bars:
        yield b


def drain(agen):
    """Collect an async generator from a sync test (no pytest-asyncio)."""
    async def go():
        return [bar async for bar in agen]
    return asyncio.run(go())


def test_aggregate_rolls_five_one_minute_bars_into_one():
    # 10 x 1m bars starting on a 5m boundary -> 1 closed 5m bucket
    # (the second bucket is still open, so it is not emitted)
    bars = ramp(10, start_ts=1_700_000_100_000)   # aligned to 5m
    out = drain(aggregate(_ayield(bars), "1m", "5m"))
    assert len(out) == 1
    assert out[0]["open"] == bars[0]["open"]
    assert out[0]["close"] == bars[4]["close"]
    assert out[0]["high"] == max(b["high"] for b in bars[:5])
    assert out[0]["low"] == min(b["low"] for b in bars[:5])
    assert out[0]["volume"] == sum(b["volume"] for b in bars[:5])


def test_aggregate_is_a_passthrough_for_the_native_interval():
    bars = ramp(3)
    assert drain(aggregate(_ayield(bars), "1m", "1m")) == bars


def test_aggregate_rejects_non_multiples():
    with pytest.raises(ValueError):
        drain(aggregate(_ayield(ramp(2)), "1h", "15m"))


# --- registry ----------------------------------------------------------

def test_registry_without_alpaca_keys_registers_binance_only(monkeypatch):
    monkeypatch.setattr(sources.config, "ALPACA_KEY_ID", "")
    monkeypatch.setattr(sources.config, "ALPACA_SECRET_KEY", "")
    registry = sources.build_registry()
    assert list(registry) == ["binance"]


def test_registry_with_alpaca_keys_registers_both(monkeypatch):
    monkeypatch.setattr(sources.config, "ALPACA_KEY_ID", "key")
    monkeypatch.setattr(sources.config, "ALPACA_SECRET_KEY", "secret")
    registry = sources.build_registry()
    assert sorted(registry) == ["alpaca", "binance"]


def test_unknown_source_is_reported(monkeypatch):
    sources.reset({"fake": FakeSource()})
    try:
        with pytest.raises(UnknownSymbol):
            sources.get("nope")
    finally:
        sources.reset(None)
