"""Central configuration. Every service is configured purely via
environment variables (12-factor) so the same image runs anywhere."""
import os

from .intervals import DEFAULT as DEFAULT_INTERVAL, INTERVALS, SUPPORTED as SUPPORTED_INTERVALS

NATS_URL = os.environ.get("NATS_URL", "nats://localhost:4222")
DB_PATH = os.environ.get("DB_PATH", "candle_agent.db")
SYMBOL = os.environ.get("SYMBOL", "AAPL").upper()

# ingest
INGEST_MODE = os.environ.get("INGEST_MODE", "demo")  # demo | live
DEMO_SEED_BARS = int(os.environ.get("DEMO_SEED_BARS", "120"))
DEMO_INTERVAL_S = float(os.environ.get("DEMO_INTERVAL_S", "3"))
BINANCE_WS = "wss://stream.binance.com:9443/ws/{stream}"

# Bar interval (Binance kline naming). Misconfiguration fails loudly at
# import rather than silently falling back - a wrong interval would
# otherwise look like an exchange that just never sends bars.
INTERVAL = os.environ.get("INTERVAL", DEFAULT_INTERVAL)
if INTERVAL not in INTERVALS:
    raise ValueError(
        f"INTERVAL={INTERVAL!r} is not supported; use one of: {SUPPORTED_INTERVALS}"
    )

# symbol list (merged across sources), cached in memory by the api service
SYMBOLS_TTL_S = float(os.environ.get("SYMBOLS_TTL_S", str(24 * 3600)))
SYMBOLS_RETRY_S = float(os.environ.get("SYMBOLS_RETRY_S", "60"))

# --- sources ---
# binance: public data, no credentials
BINANCE_REST = os.environ.get("BINANCE_REST", "https://api.binance.com")
# quote assets we trade against on Binance; everything else is filtered out
QUOTE_ASSETS = tuple(
    q.strip().upper()
    for q in os.environ.get("QUOTE_ASSETS", "USDT,USDC").split(",")
    if q.strip()
)

# alpaca: the default source. US-hosted, so it works from AWS and from
# regions where Binance answers 451, and it covers stocks as well as crypto.
ALPACA_KEY_ID = os.environ.get("ALPACA_KEY_ID", "")
ALPACA_SECRET_KEY = os.environ.get("ALPACA_SECRET_KEY", "")

# Two DIFFERENT hosts, and mixing them up costs an afternoon:
#   ALPACA_BASE_URL  trading     - assets, clock, and orders if we ever
#                                  place them. Paper and live differ here.
#   ALPACA_DATA_URL  market data - bars, quotes, trades. Same for both.
# Neither carries a version path; the code appends /v2 or /v1beta3 itself.
ALPACA_BASE_URL = os.environ.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
ALPACA_DATA_URL = os.environ.get("ALPACA_DATA_URL", "https://data.alpaca.markets")
ALPACA_FEED = os.environ.get("ALPACA_FEED", "iex")      # free tier
ALPACA_WS_EQUITY = os.environ.get(
    "ALPACA_WS_EQUITY", "wss://stream.data.alpaca.markets/v2/iex")
ALPACA_WS_CRYPTO = os.environ.get(
    "ALPACA_WS_CRYPTO", "wss://stream.data.alpaca.markets/v1beta3/crypto/us")


def _check_no_version_suffix(name: str, url: str) -> str:
    """Reject a host that already carries a version path.

    A URL ending in /v2 silently becomes /v2/v2/assets once the code
    appends its own version, and Alpaca answers 404 - which reads as
    "endpoint gone" rather than "your config is wrong". Fail at startup
    instead of at the first request.
    """
    trimmed = url.rstrip("/")
    if trimmed.endswith(("/v2", "/v1beta3", "/v1")):
        raise ValueError(
            f"{name}={url!r} must not include a version path - the code "
            f"appends it. Use {trimmed.rsplit('/', 1)[0]!r} instead.")
    return trimmed


ALPACA_BASE_URL = _check_no_version_suffix("ALPACA_BASE_URL", ALPACA_BASE_URL)
ALPACA_DATA_URL = _check_no_version_suffix("ALPACA_DATA_URL", ALPACA_DATA_URL)

# default source for a subscribe request that does not name one and whose
# symbol is not in the cached list
DEFAULT_SOURCE = os.environ.get("DEFAULT_SOURCE", "alpaca")

# api: origins allowed to call this service from a browser. The terminal
# dev server and preview server by default; set CORS_ORIGINS (comma
# separated, or "*") for a deployed frontend.
CORS_ORIGINS = [
    o.strip() for o in os.environ.get(
        "CORS_ORIGINS",
        "http://localhost:5174,http://127.0.0.1:5174,"
        "http://localhost:4173,http://127.0.0.1:4173",
    ).split(",") if o.strip()
]

# analyzer
ANALYZE_EVERY = int(os.environ.get("ANALYZE_EVERY", "1"))  # every Nth bar
MIN_BARS = int(os.environ.get("MIN_BARS", "30"))
MAX_DELIVER = int(os.environ.get("MAX_DELIVER", "3"))  # redeliveries before giving up

RISK_PER_TRADE = float(os.environ.get("RISK_PER_TRADE", "100"))  # $ risked per trade

# metrics: each service exposes Prometheus metrics on its own port
METRICS_PORT = int(os.environ.get("METRICS_PORT", "0"))  # 0 = disabled
