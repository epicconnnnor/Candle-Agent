"""Exercise the real Alpaca API and report pass/fail per check.

    python scripts/verify_alpaca.py

Reads credentials from .env (or the environment). Credentials are never
printed: every line this script emits goes through _safe(), which scrubs
the key and secret out of any string - including exception text, which is
where they most often leak.

Checks, in order (Alpaca permits one concurrent data websocket per
account, so the socket checks run strictly one at a time):

    1. auth handshake       against the data websocket
    2. list_symbols         via AlpacaSource (trading host)
    3. historical bars      last 200 1m AAPL bars (data host)
    4. market clock         is the market open, and when does it next open
    5. live websocket bar   subscribe to AAPL, wait up to 90s
    6. error path           junk symbol must raise, not hang
"""
import asyncio
import os
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

BAR_WAIT_S = 90
SECRETS: list[str] = []


def _safe(text) -> str:
    """Redact credentials from anything on its way to stdout."""
    out = str(text)
    for secret in SECRETS:
        if secret:
            out = out.replace(secret, "***REDACTED***")
    return out


def load_dotenv(path=".env"):
    """Minimal dotenv loader: KEY=value, ' #' starts an inline comment."""
    p = pathlib.Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.split(" #", 1)[0].strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), value)


load_dotenv()
SECRETS[:] = [os.environ.get("ALPACA_KEY_ID", ""),
              os.environ.get("ALPACA_SECRET_KEY", "")]

from candle_agent import config                      # noqa: E402
from candle_agent.sources.alpaca import AlpacaSource  # noqa: E402
from candle_agent.sources.base import SourceError     # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def record(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}"
          + (f"  -  {_safe(detail)}" if detail else ""))


def section(title):
    print(f"\n{title}\n" + "-" * len(title))


# --- checks ------------------------------------------------------------

async def check_auth(src):
    section("1. auth handshake (data websocket)")
    import websockets
    try:
        async with websockets.connect(config.ALPACA_WS_EQUITY,
                                      ping_interval=20, ping_timeout=10) as ws:
            await src._authenticate(ws)
            record("auth handshake", True, f"authenticated at {config.ALPACA_WS_EQUITY}")
            return True
    except Exception as e:
        record("auth handshake", False, f"{type(e).__name__}: {e}")
        return False


async def check_list_symbols(src):
    section("2. list_symbols")
    try:
        symbols = await src.list_symbols()
    except Exception as e:
        record("list_symbols", False, f"{type(e).__name__}: {e}")
        return False

    if not symbols:
        record("list_symbols", False, "returned an empty list")
        return False

    equities = [s for s in symbols if s.asset_class == "equity"]
    crypto = [s for s in symbols if s.asset_class == "crypto"]
    record("list_symbols", True,
           f"{len(symbols)} symbols ({len(equities)} equity, {len(crypto)} crypto)")
    print("        samples:")
    for s in symbols[:3]:
        print(f"          {s.symbol:<12} {s.asset_class:<8} {s.name[:44]}")
    return True


def _iso(ms):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


async def check_historical(src):
    section("3. historical bars (last 200 1m AAPL, via AlpacaSource)")
    try:
        bars = await src.history("AAPL", "1m", limit=200)
    except Exception as e:
        record("historical bars", False, f"{type(e).__name__}: {e}")
        return False

    if not bars:
        record("historical bars", False, "AlpacaSource.history() returned nothing")
        return False

    record("historical bars", True,
           f"{len(bars)} bars from {config.ALPACA_DATA_URL}")
    print(f"        first: {_iso(bars[0]['ts'])}  close={bars[0]['close']}")
    print(f"        last:  {_iso(bars[-1]['ts'])}  close={bars[-1]['close']}")

    ordered = all(bars[i]["ts"] < bars[i + 1]["ts"] for i in range(len(bars) - 1))
    record("history is ordered oldest-first", ordered)

    # A longer interval must come back natively, not rolled up from 1m.
    # Equity sessions mean gaps are legitimate overnight and at weekends,
    # so the test is that every gap is a whole multiple of the interval and
    # the smallest one IS the interval - not that they are all equal.
    try:
        hourly = await src.history("AAPL", "1h", limit=50)
        gaps = [hourly[i + 1]["ts"] - hourly[i]["ts"] for i in range(len(hourly) - 1)]
        hour = 3_600_000
        native = bool(gaps) and min(gaps) == hour and all(g % hour == 0 for g in gaps)
        record("history honours the requested interval", native,
               f"{len(hourly)} 1h bars, smallest gap {min(gaps) // 60_000}m, "
               f"largest {max(gaps) // 60_000}m (session gap)")
    except Exception as e:
        record("history honours the requested interval", False,
               f"{type(e).__name__}: {e}")
    return True


async def check_clock(src):
    section("4. market clock")
    try:
        status = await src.market_status("AAPL")
    except Exception as e:
        record("market clock", False, f"{type(e).__name__}: {e}")
        return False

    record("market clock", status.get("known") is True,
           f"is_open={status['is_open']} next_open={status['next_open']}")

    crypto = await src.market_status("BTC/USD")
    record("crypto reported as always open", crypto["is_open"] is True)
    return True


async def check_live_bar(src):
    section(f"5. live websocket bar (AAPL 1m, up to {BAR_WAIT_S}s)")
    started = time.time()
    try:
        async def first_bar():
            async for bar in src._stream_1m("AAPL", "1m"):
                return bar

        bar = await asyncio.wait_for(first_bar(), timeout=BAR_WAIT_S)
    except asyncio.TimeoutError:
        record("live websocket bar", False,
               f"no bar in {BAR_WAIT_S}s - the socket connected but stayed "
               "silent (expected outside market hours)")
        return False
    except Exception as e:
        record("live websocket bar", False, f"{type(e).__name__}: {e}")
        return False

    record("live websocket bar", True, f"received after {time.time() - started:.1f}s")
    print(f"        {bar}")
    return True


async def check_error_path(src):
    section("6. error path (junk symbol must raise, not hang)")
    try:
        async def first_bar():
            async for bar in src.stream("NOTAREALTICKER123", "1m"):
                return bar

        await asyncio.wait_for(first_bar(), timeout=30)
    except SourceError as e:
        record("junk symbol raises a classified error", True,
               f"{type(e).__name__}(kind={e.kind}, code={e.code}, retryable={e.retryable})")
        return True
    except asyncio.TimeoutError:
        record("junk symbol raises a classified error", False,
               "hung for 30s with no error - this is the silent-failure shape")
        return False
    except Exception as e:
        record("junk symbol raises a classified error", False,
               f"unclassified {type(e).__name__}: {e}")
        return False

    record("junk symbol raises a classified error", False, "yielded a bar (unexpected)")
    return False


# --- main --------------------------------------------------------------

async def main():
    print("Alpaca live verification")
    print("=" * 60)

    if not os.environ.get("ALPACA_KEY_ID") or not os.environ.get("ALPACA_SECRET_KEY"):
        print("ALPACA_KEY_ID / ALPACA_SECRET_KEY are not set - nothing to verify.")
        return 1

    key = os.environ["ALPACA_KEY_ID"]
    print(f"  key id:      ...{key[-4:]} (len {len(key)})")
    print(f"  secret:      set (len {len(os.environ['ALPACA_SECRET_KEY'])})")
    print("\n  hosts the code will actually use:")
    print(f"    trading  (ALPACA_BASE_URL):   {config.ALPACA_BASE_URL}")
    print(f"    data     (ALPACA_DATA_URL):   {config.ALPACA_DATA_URL}")
    print(f"    ws equity:                    {config.ALPACA_WS_EQUITY}")
    print(f"    ws crypto:                    {config.ALPACA_WS_CRYPTO}")
    print(f"    feed:                         {config.ALPACA_FEED}")

    src = AlpacaSource(os.environ["ALPACA_KEY_ID"], os.environ["ALPACA_SECRET_KEY"])

    # sequential: Alpaca allows one concurrent data websocket per account
    await check_auth(src)
    await check_list_symbols(src)
    await check_historical(src)
    await check_clock(src)
    await check_live_bar(src)
    await check_error_path(src)

    section("summary")
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    for name, ok, _ in RESULTS:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    print(f"\n  {passed}/{len(RESULTS)} checks passed")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        sys.exit(130)
