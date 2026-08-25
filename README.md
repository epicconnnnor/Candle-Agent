![Cover](image/Candle-Agent.png)

# Candle Agent



---

A distributed market analysis system. It streams live candle data, runs a two-stage LLM analysis over it, and pushes results to your browser in real time.

It does **not** connect to a broker and it does **not** place orders. It tells you what it sees.

---

## Features

- 📈 **Live candle ingest** — streams OHLCV from market data sources into a message bus
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

## Requirements

| Item | Requirement |
|---|---|
| OS | Linux, macOS, Windows (Docker) |
| Docker | Compose v2 |
| Python | 3.11+ (local dev only) |
| Node | 20+ (frontend only) |
| Network | Access to your configured LLM API |

---

## Quick start

```bash
git clone https://github.com/epicconnnnor/Candle-Agent.git
cd Candle-Agent
cp .env.example .env     # add your API key
docker compose up
```

Frontend:

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173

---

## Deployment

Infrastructure is defined in Terraform under [`infra/`](infra/) — VPC, ECS Fargate, ALB, ECR, Secrets Manager.

```bash
cd infra
terraform init
terraform apply
```

The frontend deploys to Cloudflare Pages on every push to `main`.

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
