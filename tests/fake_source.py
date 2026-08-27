"""A DataSource that yields a scripted bar sequence.

Lets the whole pipeline - ingest, analyzer, paper trader - be exercised
without a network, a websocket, or an exchange account.
"""
import asyncio
from collections.abc import AsyncIterator

from candle_agent.intervals import INTERVALS
from candle_agent.sources.base import (CRYPTO, Bar, DataSource, SourceError,
                                       SymbolInfo)


def ramp(n: int, start: float = 100.0, step: float = 0.5,
         step_ms: int = 60_000, start_ts: int = 1_700_000_000_000) -> list[Bar]:
    """A clean rising series - deterministic, no randomness to flake on."""
    bars = []
    price = start
    for i in range(n):
        o = price
        c = round(o + step, 4)
        bars.append({
            "ts": start_ts + i * step_ms,
            "open": o, "high": round(c + 0.1, 4),
            "low": round(o - 0.1, 4), "close": c, "volume": 10.0,
        })
        price = c
    return bars


class FakeSource(DataSource):
    """Scripted source.

    `stream_bars` are yielded one at a time; `history_bars` come back from
    history(). When `fail_with` is set the stream raises it instead, which
    is how the error-surfacing paths get tested.
    """

    name = "fake"

    def __init__(self, stream_bars=None, history_bars=None, fail_with=None,
                 symbol="FAKEUSDT", on_event=None, hang=False,
                 market_open=True, market_known=True, next_open=None):
        self.stream_bars = list(stream_bars or [])
        self.history_bars = list(history_bars or [])
        self.fail_with: SourceError | None = fail_with
        self.symbol = symbol
        self.hang = hang                # keep the stream open after the script
        self._on_event = on_event
        self.market_open = market_open
        self.market_known = market_known
        self.next_open = next_open
        self.streams_opened = 0
        self.streams_closed = 0
        self.open_calls: list[tuple[str, str]] = []
        self.history_calls: list[tuple[str, str, int]] = []

    def supported_intervals(self) -> list[str]:
        return list(INTERVALS)

    async def list_symbols(self) -> list[SymbolInfo]:
        return [SymbolInfo(symbol=self.symbol, name="Fake pair",
                           asset_class=CRYPTO, source=self.name)]

    async def history(self, symbol: str, interval: str, limit: int = 200) -> list[Bar]:
        self.history_calls.append((symbol, interval, limit))
        return list(self.history_bars[-limit:])

    async def market_status(self, symbol: str) -> dict:
        return {"is_open": self.market_open, "next_open": self.next_open,
                "next_close": None, "known": self.market_known}

    async def stream(self, symbol: str, interval: str) -> AsyncIterator[Bar]:
        self.streams_opened += 1
        self.open_calls.append((symbol, interval))
        if self._on_event:
            self._on_event({"source": self.name, "state": "connected",
                            "symbol": symbol, "interval": interval})
        try:
            if self.fail_with is not None:
                raise self.fail_with
            for bar in self.stream_bars:
                yield bar
                await asyncio.sleep(0)
            while self.hang:
                await asyncio.sleep(0.01)
        finally:
            self.streams_closed += 1
