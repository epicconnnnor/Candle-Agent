"""Binance source - public market data, no credentials required.

This is the original ingest websocket loop, moved behind DataSource:
a persistent TLS connection to <symbol>@kline_<interval>, only CLOSED
bars, reconnecting forever with exponential backoff + full jitter.

What is new is that failures are described instead of swallowed. The old
loop caught every exception and retried, which made a geo-block (HTTP
451) and a typo'd symbol both look like a hang.
"""
import asyncio
import json
import time
from collections.abc import AsyncIterator

import httpx

from .. import config
from ..intervals import INTERVALS
from ..metrics import INGEST_LAG, WS_RECONNECTS
from .base import (CRYPTO, AuthFailed, Backoff, Bar, DataSource,
                   RegionBlocked, SourceError, SourceUnavailable, StreamClosed,
                   SymbolInfo, UnknownSymbol)

REGION_BLOCKED_MSG = (
    "Binance refused the connection from this network's region (HTTP 451). "
    "Live Binance data cannot be streamed from here - deploy somewhere "
    "unrestricted, use the Alpaca source, or run INGEST_MODE=demo."
)


def _status_code(exc: BaseException):
    """HTTP status from a websocket handshake rejection, across versions."""
    resp = getattr(exc, "response", None)
    for candidate in (getattr(resp, "status_code", None), getattr(exc, "status_code", None)):
        if isinstance(candidate, int):
            return candidate
    return None


def _close_code_reason(exc: BaseException):
    """(code, reason) from a closed connection, across websockets versions."""
    for attr in ("rcvd", "sent"):
        frame = getattr(exc, attr, None)
        if frame is not None and getattr(frame, "code", None) is not None:
            return frame.code, getattr(frame, "reason", None)
    return getattr(exc, "code", None), getattr(exc, "reason", None)


def classify(exc: BaseException) -> SourceError:
    """Turn a transport exception into a SourceError the UI can explain."""
    if isinstance(exc, SourceError):
        return exc

    status = _status_code(exc)
    if status == 451:
        return RegionBlocked(REGION_BLOCKED_MSG, code=451)
    if status in (401, 403):
        return AuthFailed(f"Binance rejected the connection (HTTP {status}).", code=status)
    if status is not None:
        return SourceUnavailable(
            f"Binance handshake failed with HTTP {status}.", code=status)

    code, reason = _close_code_reason(exc)
    if code is not None:
        return StreamClosed(
            f"Binance closed the stream (code {code}"
            + (f": {reason}" if reason else "") + ").",
            code=code, reason=reason)

    return SourceUnavailable(f"Binance connection failed: {exc!r}")


class BinanceSource(DataSource):
    name = "binance"

    def __init__(self, on_event=None):
        # on_event(status_dict) lets ingest publish what happened without
        # this module knowing anything about NATS.
        self._on_event = on_event

    def supported_intervals(self) -> list[str]:
        return list(INTERVALS)          # every interval is native here

    def _emit(self, **fields):
        if self._on_event:
            self._on_event({"source": self.name, **fields})

    # --- symbols ---

    async def list_symbols(self) -> list[SymbolInfo]:
        url = f"{config.BINANCE_REST}/api/v3/exchangeInfo"
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                r = await client.get(url)
        except httpx.HTTPError as e:
            raise SourceUnavailable(f"Binance exchangeInfo unreachable: {e!r}") from e

        if r.status_code == 451:
            raise RegionBlocked(REGION_BLOCKED_MSG, code=451)
        if r.status_code >= 400:
            raise SourceUnavailable(
                f"Binance exchangeInfo returned HTTP {r.status_code}.", code=r.status_code)

        out = []
        for s in r.json().get("symbols", []):
            if s.get("status") != "TRADING":
                continue
            if s.get("quoteAsset") not in config.QUOTE_ASSETS:
                continue
            base, quote = s["baseAsset"], s["quoteAsset"]
            out.append(SymbolInfo(
                symbol=s["symbol"],
                name=f"{base}/{quote}",
                asset_class=CRYPTO,
                source=self.name,
                extra={"baseAsset": base, "quoteAsset": quote},
            ))
        out.sort(key=lambda s: s.symbol)
        return out

    # --- history ---

    async def history(self, symbol: str, interval: str, limit: int = 200) -> list[Bar]:
        """Closed klines at the requested interval, oldest first."""
        if interval not in INTERVALS:
            raise UnknownSymbol(f"unsupported interval {interval!r} for Binance.")

        url = f"{config.BINANCE_REST}/api/v3/klines"
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                r = await client.get(url, params={
                    "symbol": symbol.upper(), "interval": interval,
                    "limit": min(limit, 1000)})
        except httpx.HTTPError as e:
            raise SourceUnavailable(f"Binance klines unreachable: {e!r}") from e

        if r.status_code == 451:
            raise RegionBlocked(REGION_BLOCKED_MSG, code=451)
        if r.status_code >= 400:
            raise SourceUnavailable(
                f"Binance klines returned HTTP {r.status_code}.", code=r.status_code)

        # [open_time, o, h, l, c, v, close_time, ...]
        return [
            {"ts": k[0], "open": float(k[1]), "high": float(k[2]),
             "low": float(k[3]), "close": float(k[4]), "volume": float(k[5])}
            for k in r.json()
        ]

    # --- stream ---

    async def stream(self, symbol: str, interval: str) -> AsyncIterator[Bar]:
        import websockets      # lazy: demo mode needs no network deps

        if interval not in INTERVALS:
            raise UnknownSymbol(f"unsupported interval {interval!r} for Binance.")

        url = config.BINANCE_WS.format(
            stream=f"{symbol.lower()}@kline_{interval}")
        backoff = Backoff()
        while True:
            try:
                self._emit(state="connecting", symbol=symbol, interval=interval)
                # ping_interval/ping_timeout: detect half-open TCP connections
                # (the peer vanished but no FIN/RST ever reached us).
                async with websockets.connect(url, ping_interval=20, ping_timeout=10) as ws:
                    print(f"[binance] connected {url}")
                    self._emit(state="connected", symbol=symbol, interval=interval)
                    async for raw in ws:
                        bar = self._parse(raw)
                        if bar is not None:
                            # only a delivered bar proves the connection is
                            # healthy - opening the socket does not
                            backoff.progress()
                            yield bar
            except asyncio.CancelledError:
                raise                       # a deliberate unsubscribe, not a fault
            except Exception as e:
                err = classify(e)
                WS_RECONNECTS.inc()
                if not err.retryable:
                    # 451 and friends will never succeed on retry; surfacing
                    # beats a backoff loop that looks identical to silence.
                    self._emit(state="error", symbol=symbol, interval=interval,
                               **err.as_status())
                    print(f"[binance] fatal: {err.message}")
                    raise err from e

                delay = backoff.fail()
                if backoff.should_alert():
                    # escalate rather than loop quietly
                    self._emit(state="unhealthy", symbol=symbol, interval=interval,
                               kind="reconnecting", retryable=True,
                               attempts=backoff.attempt, retry_in_s=round(delay, 1),
                               message=(f"{backoff.attempt} consecutive failed "
                                        f"connections to Binance: {err.message}"))
                print(f"[binance] {err.message} "
                      f"reconnect #{backoff.attempt} in {delay:.1f}s")
                await asyncio.sleep(delay)

    def _parse(self, raw) -> Bar | None:
        msg = json.loads(raw)
        if "error" in msg:
            err = msg["error"]
            raise UnknownSymbol(
                f"Binance rejected the stream: {err.get('msg', err)}",
                code=err.get("code"))
        k = msg.get("k", {})
        if not k.get("x"):              # only CLOSED bars
            return None
        # one-way delay estimate: bar close time -> local arrival
        INGEST_LAG.observe(max(0.0, time.time() - k["T"] / 1000))
        return {
            "ts": k["t"],
            "open": float(k["o"]), "high": float(k["h"]),
            "low": float(k["l"]), "close": float(k["c"]),
            "volume": float(k["v"]),
        }
