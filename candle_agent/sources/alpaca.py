"""Alpaca source - US equities (IEX feed on the free tier) plus crypto.

The default source: it covers stocks, and being US-hosted it works from
AWS and from the regions where Binance answers 451.

Two hosts, and they are not interchangeable:
    config.ALPACA_BASE_URL   trading     - /v2/assets, /v2/clock
    config.ALPACA_DATA_URL   market data - /v2/stocks/.../bars
Paper and live differ on the trading host only. Using the trading host
for data (or the live host with paper keys) fails as HTTP 401/404, which
is why config refuses a URL that already carries a version path.

Alpaca streams one-minute bars only, so anything longer is rolled up
locally by `base.aggregate`; history, by contrast, is fetched at the
requested interval directly, so a chart is never waiting on a rollup.

Credentials come from ALPACA_KEY_ID / ALPACA_SECRET_KEY and are never
logged - no code path prints them or attaches them to an exception.
"""
import asyncio
import json
import math
import time
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone

import httpx

from .. import config
from ..intervals import INTERVALS, to_ms
from ..metrics import WS_RECONNECTS
from .base import (CRYPTO, EQUITY, AuthFailed, Backoff, Bar, DataSource,
                   SourceError, SourceUnavailable, StreamClosed, SymbolInfo,
                   UnknownSymbol, aggregate)

NATIVE_INTERVAL = "1m"          # the only granularity Alpaca streams

# our interval names -> Alpaca timeframe names (historical endpoint)
TIMEFRAMES = {
    "1m": "1Min", "5m": "5Min", "15m": "15Min",
    "1h": "1Hour", "4h": "4Hour", "1d": "1Day",
}

# Alpaca data-stream error codes. Anything not listed is treated as a
# retryable close.
_AUTH_CODES = {401, 402, 404}      # 403 is "already authenticated" - benign
_FATAL_CODES = {
    400: "invalid syntax - the venue rejected the subscription request",
    405: "symbol limit exceeded",
    408: "this data feed is not enabled for the account",
    409: "insufficient subscription for this feed",
    410: "invalid subscribe action for this feed",
}

CLOCK_TTL_S = 30.0              # the clock changes twice a day; poll rarely

PAGE_LIMIT = 10_000             # Alpaca's per-request maximum
MAX_PAGES = 50                  # a hard stop; 500k bars is far past any use

# A US equity session is 6.5h, and roughly 252 of 365 days are trading
# days. Used to size the history lookback: ask for too narrow a window and
# Alpaca returns only today.
SESSION_S = 6.5 * 3600
CALENDAR_PER_TRADING_DAY = 1.6      # weekends + holidays, with slack
LOOKBACK_BUFFER_DAYS = 10


def _lookback_days(interval_s: float, limit: int, around_the_clock: bool) -> int:
    """Calendar days back to ask for, to be sure of getting `limit` bars.

    Without an explicit `start`, Alpaca's bars endpoint answers with the
    current day only - 55 five-minute bars instead of 200. The window has
    to be sized from the interval, and for equities widened to step over
    nights, weekends and holidays.
    """
    if around_the_clock:
        return max(1, math.ceil(limit * interval_s / 86400) + 1)
    bars_per_day = max(1.0, SESSION_S / interval_s) if interval_s < 86400 else 1.0
    trading_days = limit / bars_per_day if interval_s < 86400 else limit
    return max(2, math.ceil(trading_days * CALENDAR_PER_TRADING_DAY)
               + LOOKBACK_BUFFER_DAYS)


def _to_ms(ts: str) -> int:
    """RFC-3339 -> epoch ms. Alpaca sends nanosecond precision."""
    text = ts.replace("Z", "+00:00")
    if "." in text:
        head, rest = text.split(".", 1)
        frac, _, tz = rest.partition("+")
        text = f"{head}.{frac[:6]}" + (f"+{tz}" if tz else "")
    return int(datetime.fromisoformat(text).timestamp() * 1000)


class AlpacaSource(DataSource):
    name = "alpaca"

    def __init__(self, key_id: str, secret_key: str, on_event=None):
        if not key_id or not secret_key:
            raise AuthFailed("Alpaca credentials are missing.")
        self._key_id = key_id
        self._secret_key = secret_key
        self._on_event = on_event
        self._classes: dict[str, str] = {}   # symbol -> asset class
        self._clock: tuple[float, dict] | None = None

    def supported_intervals(self) -> list[str]:
        # 1m is native; the rest are aggregated from it locally
        return list(INTERVALS)

    def _emit(self, **fields):
        if self._on_event:
            self._on_event({"source": self.name, **fields})

    @property
    def _headers(self) -> dict:
        return {
            "APCA-API-KEY-ID": self._key_id,
            "APCA-API-SECRET-KEY": self._secret_key,
        }

    def asset_class(self, symbol: str) -> str:
        """Alpaca writes crypto pairs with a slash; equities have none."""
        return self._classes.get(symbol) or (CRYPTO if "/" in symbol else EQUITY)

    async def _get_pages(self, url: str, params: dict, what: str, symbol: str,
                         limit: int):
        """Follow next_page_token until `limit` bars are collected.

        A single request caps out well below a useful replay window, so
        anything larger has to page. Pages come newest-first (sort=desc),
        so collecting until we have `limit` gives the most recent ones.
        """
        out: list[dict] = []
        token = None
        for _ in range(MAX_PAGES):
            page_params = dict(params, limit=min(limit - len(out), PAGE_LIMIT))
            if token:
                page_params["page_token"] = token
            payload = await self._get(url, page_params, what)

            raw = payload.get("bars") or []
            if isinstance(raw, dict):           # crypto keys bars by symbol
                raw = raw.get(symbol, [])
            out.extend(raw)

            token = payload.get("next_page_token")
            if not token or len(out) >= limit:
                break
        return out[:limit]

    async def _get(self, url: str, params: dict, what: str):
        try:
            async with httpx.AsyncClient(timeout=30.0, headers=self._headers) as client:
                r = await client.get(url, params=params)
        except httpx.HTTPError as e:
            raise SourceUnavailable(f"Alpaca {what} unreachable: {e!r}") from e

        if r.status_code in (401, 403):
            raise AuthFailed(
                f"Alpaca rejected the API credentials for {what}. Check that "
                f"the keys match the host ({url.split('/v')[0]}): paper keys "
                "do not work against the live trading host.",
                code=r.status_code)
        if r.status_code >= 400:
            raise SourceUnavailable(
                f"Alpaca {what} returned HTTP {r.status_code}: {r.text[:160]}",
                code=r.status_code)
        return r.json()

    # --- symbols (trading host) ---

    async def list_symbols(self) -> list[SymbolInfo]:
        out: list[SymbolInfo] = []
        for api_class, asset_class in (("us_equity", EQUITY), ("crypto", CRYPTO)):
            payload = await self._get(
                f"{config.ALPACA_BASE_URL}/v2/assets",
                {"status": "active", "asset_class": api_class},
                "assets")
            out.extend(
                SymbolInfo(
                    symbol=a["symbol"],
                    name=a.get("name") or a["symbol"],
                    asset_class=asset_class,
                    source=self.name,
                    extra={"exchange": a.get("exchange")},
                )
                for a in payload if a.get("tradable")
            )
        out.sort(key=lambda s: s.symbol)
        self._classes = {s.symbol: s.asset_class for s in out}
        return out

    # --- market hours (trading host) ---

    async def market_status(self, symbol: str) -> dict:
        """Crypto never closes; equities follow Alpaca's clock."""
        if self.asset_class(symbol) == CRYPTO:
            return {"is_open": True, "next_open": None, "next_close": None,
                    "known": True}

        now = time.time()
        if self._clock and now - self._clock[0] < CLOCK_TTL_S:
            return self._clock[1]

        payload = await self._get(f"{config.ALPACA_BASE_URL}/v2/clock", {}, "clock")
        status = {
            "is_open": bool(payload.get("is_open")),
            "next_open": payload.get("next_open"),
            "next_close": payload.get("next_close"),
            "known": True,
        }
        self._clock = (now, status)
        return status

    # --- history (data host) ---

    async def history(self, symbol: str, interval: str, limit: int = 200) -> list[Bar]:
        """Real historical bars at the requested interval.

        Fetched natively rather than rolled up from 1m: a 4h chart would
        otherwise need ten days of streamed minutes before it drew anything.
        """
        timeframe = TIMEFRAMES.get(interval)
        if timeframe is None:
            raise UnknownSymbol(f"unsupported interval {interval!r} for Alpaca.")

        is_crypto = self.asset_class(symbol) == CRYPTO
        interval_s = to_ms(interval) / 1000
        start = (datetime.now(timezone.utc)
                 - timedelta(days=_lookback_days(interval_s, limit, is_crypto)))

        # sort=desc + limit gives the NEWEST `limit` bars; ascending from
        # `start` would hand back the oldest ones in the window instead
        params = {"timeframe": timeframe, "limit": limit, "sort": "desc",
                  "start": start.strftime("%Y-%m-%dT%H:%M:%SZ")}
        if is_crypto:
            url = f"{config.ALPACA_DATA_URL}/v1beta3/crypto/us/bars"
            params["symbols"] = symbol
        else:
            url = f"{config.ALPACA_DATA_URL}/v2/stocks/{symbol}/bars"
            params["feed"] = config.ALPACA_FEED

        raw = await self._get_pages(url, params, "historical bars", symbol, limit)
        bars = [
            {
                "ts": _to_ms(b["t"]),
                "open": float(b["o"]), "high": float(b["h"]),
                "low": float(b["l"]), "close": float(b["c"]),
                "volume": float(b["v"]),
            }
            for b in raw
        ]
        bars.sort(key=lambda b: b["ts"])        # oldest first
        return bars

    # --- stream (data websocket) ---

    def stream(self, symbol: str, interval: str) -> AsyncIterator[Bar]:
        if interval not in INTERVALS:
            raise UnknownSymbol(f"unsupported interval {interval!r} for Alpaca.")
        return aggregate(self._stream_1m(symbol, interval), NATIVE_INTERVAL, interval)

    def _endpoint(self, symbol: str) -> str:
        return (config.ALPACA_WS_CRYPTO if self.asset_class(symbol) == CRYPTO
                else config.ALPACA_WS_EQUITY)

    async def _stream_1m(self, symbol: str, interval: str) -> AsyncIterator[Bar]:
        import websockets      # lazy: demo mode needs no network deps

        url = self._endpoint(symbol)
        backoff = Backoff()
        while True:
            try:
                self._emit(state="connecting", symbol=symbol, interval=interval)
                async with websockets.connect(url, ping_interval=20, ping_timeout=10) as ws:
                    await self._authenticate(ws)
                    await ws.send(json.dumps(
                        {"action": "subscribe", "bars": [symbol]}))
                    print(f"[alpaca] connected {url} bars={symbol}")
                    self._emit(state="connected", symbol=symbol, interval=interval)
                    async for raw in ws:
                        for bar in self._parse(raw, symbol):
                            # only a delivered bar proves the connection is
                            # healthy - opening the socket does not
                            backoff.progress()
                            yield bar
            except asyncio.CancelledError:
                raise                       # a deliberate unsubscribe, not a fault
            except Exception as e:
                err = e if isinstance(e, SourceError) else \
                    StreamClosed(f"Alpaca stream dropped: {e!r}")
                WS_RECONNECTS.inc()
                if not err.retryable:
                    self._emit(state="error", symbol=symbol, interval=interval,
                               **err.as_status())
                    print(f"[alpaca] fatal: {err.message}")
                    raise err from e

                delay = backoff.fail()
                if backoff.should_alert():
                    # escalate rather than loop quietly
                    self._emit(state="unhealthy", symbol=symbol, interval=interval,
                               kind="reconnecting", retryable=True,
                               attempts=backoff.attempt, retry_in_s=round(delay, 1),
                               message=(f"{backoff.attempt} consecutive failed "
                                        f"connections to Alpaca: {err.message}"))
                print(f"[alpaca] {err.message} "
                      f"reconnect #{backoff.attempt} in {delay:.1f}s")
                await asyncio.sleep(delay)

    async def _authenticate(self, ws) -> None:
        # The credentials go straight onto the wire; they are never printed
        # and never attached to an exception message.
        await ws.send(json.dumps({
            "action": "auth",
            "key": self._key_id,
            "secret": self._secret_key,
        }))
        deadline = asyncio.get_running_loop().time() + 10
        while asyncio.get_running_loop().time() < deadline:
            for msg in json.loads(await ws.recv()):
                kind = msg.get("T")
                if kind == "error":
                    if msg.get("code") == 403:      # already authenticated
                        return
                    raise self._stream_error(msg)
                if kind == "success" and msg.get("msg") == "authenticated":
                    return
        raise AuthFailed("Alpaca did not confirm authentication in time.")

    def _stream_error(self, msg: dict) -> SourceError:
        code, text = msg.get("code"), msg.get("msg", "stream error")
        if code in _AUTH_CODES:
            return AuthFailed(f"Alpaca auth failed: {text}", code=code)
        if code in _FATAL_CODES:
            # retrying cannot fix any of these; looping on them is exactly
            # the hot-loop-that-looks-like-a-hang failure
            return UnknownSymbol(
                f"Alpaca rejected the subscription: {text} "
                f"({_FATAL_CODES[code]})", code=code)
        return StreamClosed(f"Alpaca stream error: {text}", code=code)

    def _parse(self, raw, symbol: str) -> list[Bar]:
        out = []
        for msg in json.loads(raw):
            if msg.get("T") == "error":
                raise self._stream_error(msg)
            if msg.get("T") != "b" or msg.get("S") != symbol:
                continue
            out.append({
                "ts": _to_ms(msg["t"]),
                "open": float(msg["o"]), "high": float(msg["h"]),
                "low": float(msg["l"]), "close": float(msg["c"]),
                "volume": float(msg["v"]),
            })
        return out
