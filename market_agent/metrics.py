"""Prometheus instrumentation. Each service calls serve_metrics() once;
scrape targets are configured in deploy/prometheus.yml."""
from prometheus_client import Counter, Gauge, Histogram, start_http_server

from . import config

# --- ingest ---
BARS_INGESTED = Counter("ma_bars_ingested_total", "Closed bars ingested", ["symbol", "mode"])
WS_RECONNECTS = Counter("ma_ws_reconnects_total", "Websocket reconnect attempts")
INGEST_LAG = Histogram(
    "ma_ingest_lag_seconds",
    "Bar close time -> local arrival time (network + exchange delay)",
    buckets=(0.1, 0.25, 0.5, 1, 2, 5, 10, 30),
)

# --- analyzer ---
ANALYSES = Counter("ma_analyses_total", "Completed analyses", ["symbol", "regime", "decision"])
ANALYSIS_FAILURES = Counter("ma_analysis_failures_total", "Analyses that failed validation/retries")
LLM_LATENCY = Histogram(
    "ma_llm_pipeline_seconds",
    "End-to-end two-stage LLM pipeline latency",
    buckets=(0.5, 1, 2, 5, 10, 20, 40, 60),
)
BUS_REDELIVERIES = Counter("ma_bus_redeliveries_total", "Messages redelivered by JetStream")

# --- paper trader ---
PAPER_TRADES = Counter("ma_paper_trades_total", "Closed paper trades", ["symbol", "exit_reason"])
PAPER_TOTAL_R = Gauge("ma_paper_r_total", "Cumulative R-multiple of closed paper trades (can go down)")
PAPER_OPEN = Gauge("ma_paper_open_positions", "Currently open paper positions")

# --- api ---
SSE_CLIENTS = Gauge("ma_sse_clients", "Currently connected SSE clients")


def serve_metrics():
    """Expose /metrics on METRICS_PORT (no-op when unset)."""
    if config.METRICS_PORT:
        start_http_server(config.METRICS_PORT)
        print(f"[metrics] serving on :{config.METRICS_PORT}/metrics")
