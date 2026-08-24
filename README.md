# candle-agent

[![CI](https://github.com/epicconnnnor/Candle-Agent/actions/workflows/ci.yml/badge.svg)](https://github.com/epicconnnnor/Candle-Agent/actions/workflows/ci.yml)

Event-driven microservices pipeline: streams live market bars, computes
bar-geometry features locally, runs a **two-stage LLM analysis**
(market diagnosis �?strategy routing �?schema-validated trade decision),
and pushes results to a live web chart over SSE.

**Analysis only �?this never places orders.**

```
                 ┌──────────┐   bars.closed.*    ┌────────────┐
 Binance ─wss──► │  ingest  │ ──────────────────►│  analyzer  │──► LLM (2-stage)
 (or demo gen)   └────┬─────┘   NATS JetStream   └─────┬──────┘    + JSON schema
                      │        (at-least-once,         │           + consistency gates
                      │         durable consumer)      │ analysis.completed.*
                      ▼                                ▼
                   SQLite ◄───────────────────── ┌──────────┐   SSE    ┌─────────┐
                   (WAL)                         │   api    │ ───────► │ browser │
                                                 └──────────┘          └─────────┘
```

Three independently deployable services sharing one image:

| service    | job                                                        | metrics |
|------------|------------------------------------------------------------|---------|
| `ingest`   | persistent TLS websocket to Binance; reconnect w/ exponential backoff + full jitter; publishes each closed bar | :9101 |
| `analyzer` | durable JetStream pull consumer; two-stage LLM pipeline; explicit ACK after persist | :9102 |
| `paper`    | forward paper trading: simulates fills/stops/targets against live bars; crash-safe (recovers open positions from DB) | :9103 |
| `api`      | REST + SSE fan-out; k8s-style `/healthz`; Prometheus `/metrics` | :8000 |

## Quickstart (offline demo �?zero keys, zero market data)

```bash
docker compose up --build
# http://localhost:8000  �?live chart, synthetic bar every 3s, mock LLM
# http://localhost:8222  �?NATS monitoring
```

## Live mode

```bash
cp .env.example .env      # set INGEST_MODE=live, LLM_PROVIDER=openai_compat + key
docker compose up --build
```

Any OpenAI-compatible provider works (DeepSeek, OpenRouter, Groq, OpenAI):
`LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY`.

## Kill a service, lose nothing

```bash
docker compose kill analyzer     # bars keep arriving, pile up in JetStream
sleep 15
docker compose start analyzer    # backlog is redelivered and drained
```

The analyzer only ACKs a bar after its analysis is stored. Un-ACKed
messages are redelivered (`ack_wait=120s`), so a crash mid-analysis is
retried �?on a peer replica if you've scaled out:

```bash
docker compose up --scale analyzer=3    # JetStream load-balances the durable consumer
```

## Paper trading (forward test, zero execution risk)

The `paper` service turns the analyzer's calls into simulated trades:

- limit/stop orders fill when a live bar's range touches the entry;
  market orders fill at next open; unfilled orders expire after 20 bars
- open positions are checked for stop/target every bar; **pessimistic
  same-bar rule**: if one bar covers both, the stop is assumed first
- one trade per symbol; a newer signal replaces a *pending* order but
  never touches an *open* position
- results in R-multiples + fixed-$-risk P&L (`RISK_PER_TRADE`, default $100)
- `GET /api/paper/BTCUSDT` �?active position, history, win rate, total R
- fills are frictionless (no fees/slippage) �?a known limitation

The fill logic is pure functions (`candle_agent/paper.py`), so the
future backtest harness replays historical bars through the exact same
code that runs live.

## Design decisions

- **Numbers, not screenshots.** The LLM receives a text table of bars +
  precomputed features (EMA20, ATR14, inside/outside bars, breakout
  follow-through). Deterministic, cheap, nothing to hallucinate.
- **Two-stage with routing.** Stage 1 diagnoses the regime; the regime
  selects a strategy playbook prompt (trend vs range) for stage 2.
- **Trust nothing.** Every LLM response is validated against a JSON
  schema *and* consistency gates (a long whose stop is above entry is
  rejected). Invalid output is retried with the error fed back; after
  `MAX_DELIVER` bus redeliveries the message is TERMed (poison-message
  handling).
- **no_trade is a first-class answer.** The gates force it when the
  risk/reward math doesn't work.
- **Event-driven end to end.** Even the UI's "Run analysis" button just
  publishes `analysis.request.*`; the result arrives over SSE like any
  other bus event. The API service never runs the LLM.
- **12-factor.** One image, config via env only; no start-order
  assumptions (services retry the bus at startup).

## Networking & infra concepts on display

TLS websockets w/ ping-pong half-open detection · exponential backoff w/
full jitter · pub/sub + durable consumers + at-least-once delivery +
explicit ACK/NAK/TERM · idempotent consumers (dedupe on bar ts) ·
one-way-delay measurement (`ma_ingest_lag_seconds`) · SSE fan-out ·
DNS-based service discovery in compose/k8s · liveness probes ·
Prometheus counters/histograms per service · SQLite WAL for
multi-process concurrency.

## Tests

```bash
pytest        # 19 tests: features, pipeline, consistency gates, fill simulation
```

## Roadmap

- [ ] Prometheus + Grafana containers, dashboard JSON, SLO alert rules
- [ ] Kubernetes manifests (probes, HPA on analyzer, NetworkPolicies)
- [ ] Backtest harness: replay historical bars, score the agent's calls
- [ ] Multi-model comparison (DeepSeek vs Claude vs Llama)
- [ ] Terraform: VPC + EC2/k3s on AWS

## Disclaimer

Educational project. Not financial advice. The author is fully aware an
LLM reading candles will not beat the market �?that's not the point.