"""Data source interface.

A source knows two things: which symbols it can serve, and how to stream
closed bars for one of them. Everything venue-specific - REST shapes,
websocket handshakes, auth - lives behind this. Ingest only ever sees
`SymbolInfo` and `Bar`.

`Bar` is deliberately the exact dict ingest already publishes on the bus,
so adding sources does not change the NATS message format.
"""
import random
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from ..intervals import to_ms

# A closed bar:
#   {"ts": <epoch ms, bar OPEN time>, "open": float, "high": float,
#    "low": float, "close": float, "volume": float}
Bar = dict[str, Any]

CRYPTO = "crypto"
EQUITY = "equity"


@dataclass(frozen=True)
class SymbolInfo:
    """One tradable instrument, normalized across venues."""

    symbol: str
    name: str
    asset_class: str        # CRYPTO | EQUITY
    source: str             # registry key of the source that serves it
    extra: dict = field(default_factory=dict)   # venue-specific detail

    def as_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "asset_class": self.asset_class,
            "source": self.source,
            **self.extra,
        }


# --- errors ------------------------------------------------------------
# Sources raise these instead of returning nothing. Ingest classifies them
# into a status event; nothing is allowed to fail silently.


class SourceError(RuntimeError):
    """Base for source failures. `kind` drives the status event the UI shows."""

    kind = "error"
    retryable = True

    def __init__(self, message: str, *, code=None, reason: str | None = None):
        super().__init__(message)
        self.message = message
        self.code = code                # HTTP status or websocket close code
        self.reason = reason            # close reason / server-supplied text

    def as_status(self) -> dict:
        return {
            "kind": self.kind,
            "code": self.code,
            "reason": self.reason,
            "message": self.message,
            "retryable": self.retryable,
        }


class RegionBlocked(SourceError):
    """The venue refuses this network's region (Binance answers HTTP 451)."""

    kind = "region_blocked"
    retryable = False


class AuthFailed(SourceError):
    """Credentials rejected or missing."""

    kind = "auth"
    retryable = False


class UnknownSymbol(SourceError):
    """The venue does not list this symbol."""

    kind = "unknown_symbol"
    retryable = False


class StreamClosed(SourceError):
    """The stream ended; reconnecting is expected to help."""

    kind = "closed"


class SourceUnavailable(SourceError):
    """Transport-level failure - DNS, TLS, timeout, 5xx."""

    kind = "unavailable"


# --- interface ---------------------------------------------------------


class DataSource(ABC):
    """One trading venue."""

    name: str = ""

    @abstractmethod
    async def list_symbols(self) -> list[SymbolInfo]:
        """Every symbol this source can stream. Raises SourceError."""

    @abstractmethod
    def stream(self, symbol: str, interval: str) -> AsyncIterator[Bar]:
        """Yield closed bars until cancelled.

        Implemented as an async generator, so callers use it directly:
        `async for bar in source.stream(sym, iv)`. Raises SourceError on a
        failure the caller should surface; transient reconnects are the
        source's own business.
        """

    @abstractmethod
    def supported_intervals(self) -> list[str]:
        """Interval names this source accepts, in ascending duration order."""

    async def history(self, symbol: str, interval: str, limit: int = 200) -> list[Bar]:
        """Closed bars from before the stream opened, oldest first.

        Stored but not republished, so a chart has something to draw
        immediately. Sources without a backfill endpoint return nothing.
        """
        return []

    async def market_status(self, symbol: str) -> dict:
        """Whether this symbol is tradable right now.

        `{"is_open": bool, "next_open": str|None, "next_close": str|None,
          "known": bool}`. `known` is False when the source cannot say, so
          callers report "unknown" instead of asserting a market is open.
        Venues that never close (crypto) answer True.
        """
        return {"is_open": True, "next_open": None, "next_close": None, "known": False}

    async def aclose(self) -> None:
        """Release long-lived resources. Overridden where there are any."""


# --- reconnect pacing --------------------------------------------------


class Backoff:
    """Exponential backoff with full jitter, and an escalating alarm.

    The subtlety this exists for: a reconnect counter must not reset just
    because the socket opened. Subscribing to a symbol the venue accepts
    but never sends data for connects successfully every time and fails
    immediately after, so a connect-keyed counter stays at 1 forever and
    the "backoff" becomes a hot loop hammering the venue.

    So the counter is reset by `progress()` - an actual bar arriving -
    and by nothing else.
    """

    def __init__(self, base_s: float = 1.0, cap_s: float = 60.0,
                 alert_every: int = 5):
        self.base_s = base_s
        self.cap_s = cap_s
        self.alert_every = alert_every
        self.attempt = 0

    def progress(self) -> None:
        """A bar arrived: the connection is genuinely healthy."""
        self.attempt = 0

    def fail(self) -> float:
        """Count a failure and return how long to wait before retrying."""
        self.attempt += 1
        ceiling = min(self.cap_s, self.base_s * 2 ** self.attempt)
        return random.uniform(0, ceiling)       # full jitter

    def should_alert(self) -> bool:
        """True on every Nth consecutive failure, so repeated trouble is
        escalated instead of scrolling past as identical retry lines."""
        return self.attempt > 0 and self.attempt % self.alert_every == 0


# --- helpers shared by sources ----------------------------------------


def new_bucket(bar: Bar, bucket_ts: int) -> Bar:
    return {
        "ts": bucket_ts,
        "open": bar["open"], "high": bar["high"],
        "low": bar["low"], "close": bar["close"],
        "volume": bar["volume"],
    }


def merge_into(bucket: Bar, bar: Bar) -> None:
    bucket["high"] = max(bucket["high"], bar["high"])
    bucket["low"] = min(bucket["low"], bar["low"])
    bucket["close"] = bar["close"]
    bucket["volume"] = round(bucket["volume"] + bar["volume"], 8)


async def aggregate(bars: AsyncIterator[Bar], base: str, target: str) -> AsyncIterator[Bar]:
    """Roll a stream of `base` bars up into `target` bars.

    For venues that only stream one granularity (Alpaca streams 1m). A
    bucket is emitted when a bar belonging to the NEXT bucket arrives, so
    only closed buckets are ever published - same contract as a native
    interval. The cost is that the first bar takes up to one target
    interval to appear.
    """
    base_ms, target_ms = to_ms(base), to_ms(target)
    if target_ms == base_ms:
        async for bar in bars:
            yield bar
        return
    if target_ms % base_ms:
        raise ValueError(f"cannot aggregate {base} into {target}")

    bucket: Bar | None = None
    async for bar in bars:
        bucket_ts = bar["ts"] - (bar["ts"] % target_ms)
        if bucket is None:
            bucket = new_bucket(bar, bucket_ts)
        elif bucket_ts == bucket["ts"]:
            merge_into(bucket, bar)
        else:
            yield bucket
            bucket = new_bucket(bar, bucket_ts)
