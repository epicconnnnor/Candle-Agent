# Candle Agent

📈 I built this to answer a question I kept asking myself while staring at charts: what is this market actually doing right now?

It pulls live candle data, hands it to an LLM in two stages, and streams the answer to your browser as it thinks.

It does not place trades. It doesn't touch your broker. It just tells you what it sees.

---

## What it does

🕯️ Pulls candles from live market data sources
🧠 Runs a two-stage analysis — first diagnose the market, then decide on a trade
🛡️ Checks every response the model gives back, and retries when it's malformed
📡 Pushes results to the browser over SSE as they land
📊 Draws the entry, stop, and target right on the chart

---

## Why two stages

I tried doing it in one prompt first. The model would jump straight to "buy here" without ever establishing what kind of market it was looking at, and the answers were all over the place.

So I split it:

**Stage 1** just describes the market. Cycle phase, trend structure, key levels, how confident it is. No trade talk allowed.

**Stage 2** reads that diagnosis and decides what to do about it. Entry, stop, target, and why. Or it says don't trade, which is a valid answer and happens often.

Separating them made the output far more stable. Stage 2 can't hallucinate a setup that contradicts stage 1, because stage 1 already committed to a description.

Both stages return strict JSON. If the model returns something broken, I repair it or retry before it ever reaches your screen — that validation layer ended up being a surprising amount of the code.

---

## How it's put together

```
data source ──▶ ingest ──▶ NATS JetStream ──▶ analysis ──▶ SSE ──▶ browser
                                │                 │
                                ▼                 ▼
                             SQLite          Prometheus
```

This started as one big Python script. I broke it apart because a slow LLM call was blocking data ingestion, and I wanted to be able to restart the analysis service without dropping candles.

Now each piece runs in its own container and talks only through NATS. Nothing shares a database connection.

---

## Stack

| Layer | What I used |
|---|---|
| Services | Python |
| Messaging | NATS JetStream |
| Storage | SQLite (WAL) |
| Transport | Server-Sent Events |
| Metrics | Prometheus |
| Frontend | React, TypeScript, Vite, Tailwind |
| Charting | lightweight-charts |
| Packaging | Docker |
| CI | GitHub Actions |

---

## Try it

```bash
git clone https://github.com/epicconnnnor/Candle-Agent.git
cd Candle-Agent
cp .env.example .env     # drop your API key in here
docker compose up
```

Then the frontend:

```bash
cd frontend
npm install
npm run dev
```

---

## Tests

```bash
pytest
ruff check .
```

CI runs pytest, ruff, an encoding check, and a full compose build with health checks on every push.

---

## What's next

- [ ] Get it onto AWS — ECS Fargate, ALB, Secrets Manager
- [ ] Swap SQLite for Postgres
- [ ] Run history, and shareable links to a past analysis
- [ ] Replay mode so I can backtest the agent against old data

---

## License

MIT