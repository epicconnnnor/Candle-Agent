"""Offline synthetic source - the INGEST_MODE=demo path.

Deliberately absent from the public registry: it serves no real venue, so
/symbols must never advertise it. Ingest selects it directly when
INGEST_MODE=demo, which keeps demo and live on one code path.
"""
import asyncio
import random
from collections.abc import AsyncIterator

from .. import config
from ..demo import next_bar, synthetic_bars
from ..intervals import INTERVALS, to_ms
from .base import CRYPTO, Bar, DataSource, SymbolInfo


class DemoSource(DataSource):
    name = "demo"

    def __init__(self, on_event=None, emit_every_s: float | None = None):
        self._on_event = on_event
        # wall-clock cadence between emitted bars; unrelated to the bar's
        # own interval, so a 4h chart still fills in seconds
        self._emit_every_s = (
            config.DEMO_INTERVAL_S if emit_every_s is None else emit_every_s)
        self._last: Bar | None = None       # continuity between history and stream

    def supported_intervals(self) -> list[str]:
        return list(INTERVALS)

    async def list_symbols(self) -> list[SymbolInfo]:
        return [SymbolInfo(symbol=config.SYMBOL, name="Demo series",
                           asset_class=CRYPTO, source=self.name)]

    async def history(self, symbol: str, interval: str, limit: int = 200) -> list[Bar]:
        # DEMO_SEED_BARS wins over the caller's limit: how much history the
        # offline demo starts with is a property of the demo, not the caller
        bars = synthetic_bars(config.DEMO_SEED_BARS, step_ms=to_ms(interval))
        self._last = bars[-1]
        return bars

    async def stream(self, symbol: str, interval: str) -> AsyncIterator[Bar]:
        step_ms = to_ms(interval)
        rng = random.Random()
        # continue the seeded series so the live tail joins the history
        last = self._last or synthetic_bars(1, step_ms=step_ms)[-1]
        if self._on_event:
            self._on_event({"source": self.name, "state": "connected",
                            "symbol": symbol, "interval": interval})
        while True:
            await asyncio.sleep(self._emit_every_s)
            last = next_bar(last, rng, step_ms=step_ms)
            yield last
