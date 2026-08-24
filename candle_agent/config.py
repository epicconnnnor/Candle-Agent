"""Central configuration. Every service is configured purely via
environment variables (12-factor) so the same image runs anywhere."""
import os

NATS_URL = os.environ.get("NATS_URL", "nats://localhost:4222")
DB_PATH = os.environ.get("DB_PATH", "candle_agent.db")
SYMBOL = os.environ.get("SYMBOL", "BTCUSDT").upper()

# ingest
INGEST_MODE = os.environ.get("INGEST_MODE", "demo")  # demo | live
DEMO_SEED_BARS = int(os.environ.get("DEMO_SEED_BARS", "120"))
DEMO_INTERVAL_S = float(os.environ.get("DEMO_INTERVAL_S", "3"))
BINANCE_WS = "wss://stream.binance.com:9443/ws/{stream}"

# analyzer
ANALYZE_EVERY = int(os.environ.get("ANALYZE_EVERY", "1"))  # every Nth bar
MIN_BARS = int(os.environ.get("MIN_BARS", "30"))
MAX_DELIVER = int(os.environ.get("MAX_DELIVER", "3"))  # redeliveries before giving up

RISK_PER_TRADE = float(os.environ.get("RISK_PER_TRADE", "100"))  # $ risked per trade

# metrics: each service exposes Prometheus metrics on its own port
METRICS_PORT = int(os.environ.get("METRICS_PORT", "0"))  # 0 = disabled
