# Technical guide

[Home](../README.md) · [Installation](installation.md) · [Contributing](../CONTRIBUTING.md)

This guide covers the local application's structure and evaluation tools. Start with the installation guide if you only want to use the interface.

## Repository map

```text
Candle-Agent/
  README.md                 Product overview and starting points
  LICENSE                   MIT license
  CONTRIBUTING.md           Development setup and checks
  assets/                   Screenshots used by documentation
  docs/                     User, installation, and technical guides
  candle_agent/             Python application
    services/               Ingest, API, analyzer, replay, paper, scorer
    sources/                Alpaca, Binance, and synthetic source adapters
    prompts/                Model instructions and strategy reference inputs
    demo_samples/           Saved examples served by the API
    static/                 Basic chart page served on port 8000
  terminal/                 Main React browser interface on port 5174
    src/                    Components, API client, hooks, and chart helpers
  scripts/                  Maintenance and verification utilities
  tests/                    Python tests
  .github/                  Continuous integration and encoding check
  .env.example              Backend settings template
  docker-compose.yml        Local services, ports, and database volume
  Dockerfile                Shared Python service image
  requirements.txt          Python dependencies
  requirements-dev.txt      Development dependencies
  ruff.toml                 Python lint configuration
```

The Markdown files under `candle_agent/prompts/docs/` are runtime model inputs. They are kept with the application; user-facing documentation belongs in the top-level `docs/`.

## Services and request paths

| Service | Responsibility |
| --- | --- |
| `nats` | Messaging between processes |
| `ingest` | Source subscriptions, candle storage, closed-bar events |
| `analyzer` | Background and queued two-stage analyses |
| `api` | REST endpoints, event stream, inline key-based analysis, follow-up, scoring |
| `replay` | Re-submit stored historical bars with an as-of bound |
| `paper` | Simulate paper outcomes; does not send broker orders |

The scorer is invoked through the API; it is not a separate Compose service.

Python services share SQLite storage in the `agent-data` volume. The frontend uses REST and Server-Sent Events. A browser-key analysis runs inline in the API process so the key does not enter durable messaging. Other analysis requests can be queued. Follow-up requests run through the API.

The main browser interface is React, TypeScript, Vite, Tailwind, and lightweight-charts. The backend uses FastAPI, NATS JetStream, SQLite, and Prometheus metrics.

See [architecture](architecture.md) for the reasoning behind the split, validation, and historical-data boundaries.

## Local endpoints

| Address | Purpose |
| --- | --- |
| [localhost:5174](http://localhost:5174) | Main browser interface |
| [localhost:8000/healthz](http://localhost:8000/healthz) | Backend readiness |
| [localhost:8000/docs](http://localhost:8000/docs) | Interactive API reference |
| [localhost:8000](http://localhost:8000) | Separate basic chart page |
| [localhost:8222](http://localhost:8222) | NATS monitoring |

## Replay and score history

Replay is an advanced, optional workflow. It analyzes bars already stored in the database; it does not download a historical dataset for you. A fresh installation or the saved-example JSON files alone is not enough for a meaningful scoring run.

Use real stored bars for the chosen symbol and interval, with enough earlier bars to build each snapshot and enough later bars to score outcomes. Use matching data mode and prompt configuration across services. Replay requires `ANALYZE_EVERY=1`; apply any change with `docker compose up -d`.

Replay uses the analyzer's configured model credentials, not a key entered only in the browser. With a real model, replay incurs provider charges. Two-stage analyses and retries mean the analysis count is not a count of individual HTTP calls.

### Estimate first

Open [the API reference](http://localhost:8000/docs), expand **POST /api/replay**, and click **Try it out**. Use this request body:

```json
{
  "symbol": "AAPL",
  "interval": "1m",
  "stride": 30,
  "max_analyses": 12,
  "dry_run": true
}
```

Execute it and inspect the available-bar count and estimate. This dry run does not perform model analyses. Estimates are not a fixed-price guarantee.

`max_analyses` is required. `stride=30` spaces decision bars 30 bars apart, which helps avoid overlapping evaluation windows under the default 30-bar scoring horizon.

### Run and inspect

When ready, submit the same body with `"dry_run": false`. Record the returned run ID. Use **GET /api/replay/{run_id}** to inspect its progress, or **POST /api/replay/{run_id}/stop** to request that the run stop.

### Score the run

After the run finishes and forward bars are available, use **POST /api/score**:

```json
{
  "symbol": "AAPL",
  "interval": "1m",
  "replay_run_id": 123
}
```

Replace `123` with your actual run ID. Compatible runs may be supplied as an array of IDs. Scoring reads stored data and does not make model calls.

A withheld score is a valid result: too few independent forward windows or resolved trades cannot support every metric. Read the [scoring design](scoring-design.md) before interpreting the [published results](results.md).

## Development and maintenance

Use [Contributing](../CONTRIBUTING.md) for development dependencies, tests, and frontend checks.

The scripts under `scripts/` cover provider verification, exporting examples, data migration, and service verification. Read a script before running it: some contact providers or modify stored data. Normal local installation only needs the commands in the installation guide.
