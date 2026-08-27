![Cover](image/Candle-Agent.png)

# Candle Agent



---

A distributed market analysis system. It streams live candle data, runs a two-stage LLM analysis over it, and pushes results to your browser in real time.

It does **not** connect to a broker and it does **not** place orders. It tells you what it sees.

---

## Features

- 📈 **Live candle ingest** — streams OHLCV from Alpaca (stocks + crypto) or Binance
- 🌐 **Pluggable sources** — crypto and US equities behind one `DataSource` interface
- ⏱️ **Selectable timeframe** — 1m, 5m, 15m, 1h, 4h, 1d
- 🧠 **Two-stage AI analysis** — market diagnosis, then trade decision (limit / breakout / market / no-trade)
- 🛡️ **Validation layer** — JSON schema checks, semantic consistency, truncation repair, auto-retry
- 📡 **Real-time push** — Server-Sent Events, no polling
- 📊 **Trading terminal UI** — candlestick chart with entry, stop, and target drawn on it
- 🔀 **Service isolation** — ingest, analysis, and streaming run independently over NATS
- 📉 **Metrics** — Prometheus on every service
- ☁️ **Cloud-native** — Terraform-provisioned AWS ECS Fargate deployment

---

## Why two stages

I tried a single prompt first. The model would jump straight to "buy here" without establishing what kind of market it was looking at, and the answers were unstable.

So I split it:

**Stage 1** describes the market only. Cycle phase, trend structure, key levels, confidence. No trade talk allowed.

**Stage 2** reads that diagnosis and decides what to do. Entry, stop, target, reason. Or no trade — which happens often and is a valid answer.

Stage 2 can't invent a setup that contradicts Stage 1, because Stage 1 already committed to a description. That one change did more for output quality than any prompt tuning.

---

## Architecture

```
data source ──▶ ingest ──▶ NATS JetStream ──▶ analysis ──▶ SSE ──▶ browser
                                │                 │
                                ▼                 ▼
                             SQLite          Prometheus
```

This started as one script. I broke it apart because a slow LLM call was blocking data ingestion, and I wanted to restart the analysis service without dropping candles.

Full design notes: [`docs/architecture.md`](docs/architecture.md)

---

## Data sources

Every venue sits behind `DataSource` (`candle_agent/sources/base.py`), so
ingest only ever sees a `SymbolInfo` and a stream of `Bar` dicts.

| Source | Assets | Credentials | Stream intervals | History |
|---|---|---|---|---|
| `alpaca` **(default)** | US equities + crypto | `ALPACA_KEY_ID`, `ALPACA_SECRET_KEY` | 1m native, longer rolled up locally | native, all six |
| `binance` | crypto (USDT/USDC pairs) | none | all six, native | native, all six |
| `demo` | synthetic | none | all six (`INGEST_MODE=demo`) | synthetic |

**Alpaca is the default** (`DEFAULT_SOURCE=alpaca`, default symbol `AAPL`).
Binance stays registered and `BTCUSDT` still works through it — but it
answers **HTTP 451** from US IPs, AWS included, so it cannot be the
default path. That 451 surfaces as a `region_blocked` status event rather
than a silent reconnect loop.

The registry is built from whichever credentials are present. With no
Alpaca keys it registers Binance alone and says so at startup — not a
crash.

### Alpaca hosts

Two different hosts, and mixing them up costs an afternoon:

| Variable | Purpose | Default |
|---|---|---|
| `ALPACA_BASE_URL` | trading — assets, clock | `https://paper-api.alpaca.markets` |
| `ALPACA_DATA_URL` | market data — bars | `https://data.alpaca.markets` |

Neither may end in a version path. The code appends `/v2` (or
`/v1beta3`) itself, so a URL ending in `/v2` becomes `/v2/v2/assets` and
returns 404 — which reads as "endpoint gone" rather than "config wrong".
**Startup refuses such a URL with an explicit error.** Note also that
paper keys are rejected by the live trading host: paper keys go with
`paper-api.alpaca.markets`.

### History limits on the free plan

`/subscribe` asks for 200 bars at the requested interval. Alpaca's free
IEX plan caps how far intraday history goes back — measured, not guessed:

| Interval | AAPL bars returned |
|---|---|
| 1m, 5m, 15m, 1h | 200 (full) |
| 4h | ~60 — equity intraday history stops around 35 days back |
| 1d | 200 (full) |

Crypto intraday is capped tighter, around 7 days (`BTC/USD` 1h returns
~168). Widening the request window does not help; it is a plan limit. A
short backfill is reported as a `backfilled` status event with
`partial: true`, so a stubby chart explains itself.

Adding a venue means one file implementing four methods, plus a line in
`sources/__init__.py`.

### Connection status

Ingest publishes what the feed is doing on `ingest.status.<SYMBOL>`,
forwarded to the browser over SSE. Silence is never left unexplained:

| State | Meaning |
|---|---|
| `connected` / `streaming` | socket open; bars flowing |
| `market_closed` | equity outside market hours, with `next_open` — history is still returned |
| `stalled` | no bar for 2× the interval **while the clock says the market is open** |
| `unhealthy` | N consecutive failed reconnects, with the attempt count |
| `region_blocked` | Binance 451 |
| `backfill_failed` | history fetch failed; the live stream continues |

`market_closed` and `stalled` are distinguished by Alpaca's clock
endpoint, not guessed from the absence of data.

---

## API

| Route | What it does |
|---|---|
| `GET /symbols` | Tradable symbols merged across sources, cached 24h. Filter with `?source=` / `?asset_class=`. `unavailable` names any source that failed. |
| `POST /subscribe` | `{symbol, interval, source?}` — repoints ingest, backfills 200 real historical bars at the requested interval, and returns them so the chart renders immediately. Idempotent: the same arguments twice is a no-op, not a second socket. Unknown symbol → 400. |
| `GET /api/bars/{symbol}` | Stored bars. |
| `GET /api/analysis/{symbol}` | Latest two-stage analysis. |
| `POST /api/analyze/{symbol}` | Queue an analysis (202; result arrives over SSE). |
| `GET /api/paper/{symbol}` | Paper position, history, stats. |
| `GET /api/events` | SSE: bars, analyses, paper updates, and ingest connection status. |

`source` is optional on `/subscribe` — it is inferred from the symbol's
entry in the cached catalogue.

---

## Bring your own key

Visitors can run analyses on their own LLM credentials instead of the
server's. The security model is the whole feature; everything else is
plumbing.

- The key lives in **React state only**. Not localStorage, not
  sessionStorage, not a cookie, not a database, not disk. A reload clears
  it. There is no "remember my key", by design.
- It travels in an **`X-LLM-Key` header**, never a URL or query string —
  URLs end up in browser history, proxy logs and access logs.
- The backend uses it for **one upstream call** and discards it. It is
  never written to a log, an error message, a trace, or a stored record.
- Provider error bodies are **not forwarded** on an auth failure. They
  echo the key back — DeepSeek returns a masked `****ghij` tail that no
  scrubbing regex can recognise — so the body is dropped and replaced with
  a fixed message.
- The header is **refused over plain HTTP** unless the host is loopback
  (for local development) or `ALLOW_INSECURE_KEY_HEADER=true`.

A visitor key makes the analysis run **inline in the api process**, not
over the bus. That is deliberate: JetStream persists messages to disk, so
putting a key on a request subject would write it down.

`tests/test_byok_security.py` asserts the key never appears in stdout,
stderr, the response body, any database row, or any bus message after a
real analysis — driving the actual client code, with only the outbound
HTTP call intercepted.

| Variable | Default | Purpose |
|---|---|---|
| `RATE_LIMIT_PER_HOUR` | `60` | Per-IP cap on analyze. `0` disables. |
| `TRUST_PROXY_HEADERS` | `false` | Honour `X-Forwarded-For`. Only behind a proxy that rewrites it — it is otherwise trivially spoofed. |
| `ALLOW_INSECURE_KEY_HEADER` | `false` | Accept a key over plain HTTP. Never in production. |

Rate limiting applies **by IP regardless of whose key is used** — a
visitor's key protects their wallet, not this server's CPU. The counter is
in-memory and therefore per replica.

---

## Terminal

The terminal reads live data only — there is no mock mode. On open it
fetches `/symbols`, subscribes to the last symbol you picked, draws the
returned history, and then follows `/api/events`.

- **Symbol picker** — searchable combobox over every symbol the sources
  report (13k+ with Alpaca), matching on ticker or company name, grouped
  by asset class with your last five picks pinned on top.
- **Live updates** — the newest candle goes in via `series.update()`, so a
  tick never re-sets the whole series.
- **Status is visible** — every `ingest.status` event is rendered: a muted
  banner with the next open when the market is closed, a note when the
  backfill came back short, and a bear-coloured bar for `region_blocked`,
  `unknown_symbol` or repeated reconnect failures. An empty chart is never
  left unexplained.
- **Connection state** — `connected` / `reconnecting` / `disconnected` sits
  in the top bar; the SSE stream reconnects on its own with jittered
  exponential backoff.
- **Switching symbols** dims the old chart rather than blanking it.

---

## Requirements

| Item | Requirement |
|---|---|
| OS | Linux, macOS, Windows (Docker) |
| Docker | Compose v2 |
| Python | 3.11+ (local dev only) |
| Node | 20+ (trading terminal only) |
| Network | Access to your configured LLM API |

---

## Quick start

```bash
git clone https://github.com/epicconnnnor/Candle-Agent.git
cd Candle-Agent
cp .env.example .env     # add your API key
docker compose up
```

Trading terminal:

```bash
cd terminal
cp .env.example .env      # optional; defaults to http://localhost:8000
npm install
npm run dev
```

Open http://localhost:5174

The terminal talks to the api over `VITE_API_URL` (default
`http://localhost:8000`). Because it runs on its own origin, the api must
allow it — that is what `CORS_ORIGINS` is for, and the default already
covers the dev and preview servers.

---

## Deployment

Infrastructure is defined in Terraform under [`infra/`](infra/) — VPC, ECS Fargate, ALB, ECR, Secrets Manager.

```bash
cd infra
terraform init
terraform apply
```

The trading terminal (`terminal/`) deploys to Cloudflare Pages on every push to `main`.

---

## Tests

```bash
pytest
ruff check .
```

CI runs pytest, ruff, a UTF-8 encoding check, and a full compose build with health checks on every push.

---

## Stack

| Layer | Tech |
|---|---|
| Services | Python |
| Messaging | NATS JetStream |
| Storage | SQLite (WAL) |
| Transport | Server-Sent Events |
| Metrics | Prometheus |
| Frontend | React, TypeScript, Vite, Tailwind |
| Charting | lightweight-charts |
| Infrastructure | Terraform, AWS ECS Fargate |
| CI | GitHub Actions |

---

## Roadmap

- [ ] Postgres in place of SQLite
- [ ] Run history and shareable analysis links
- [ ] Replay mode for backtesting the agent against historical data

---

**Disclaimer** — This tool is for learning and research. It is not investment advice. Trading carries risk; your decisions are your own.

## License

MIT
