# Configuration

[Home](../README.md) · [Installation](installation.md) · [Troubleshooting](troubleshooting.md)

Edit the project-root `.env` for backend settings. After a change, run `docker compose up -d` from the project folder. Do not overwrite an existing `.env` when updating.

The browser's default API address is `http://localhost:8000`. Only create `terminal/.env` if you need to change a frontend setting; use [terminal/.env.example](../terminal/.env.example) as its reference and restart `npm run dev` afterward.

## Common backend settings

| Setting | Purpose | Example/default |
| --- | --- | --- |
| `INGEST_MODE` | Synthetic or real candles | `demo` / `live` |
| `SYMBOL` | Initial backend symbol | `AAPL` |
| `INTERVAL` | Initial interval | `1m` |
| `DEFAULT_SOURCE` | Preferred real source | `alpaca` / `binance` |
| `ALPACA_KEY_ID`, `ALPACA_SECRET_KEY` | Enable Alpaca | Empty until configured |
| `ALPACA_BASE_URL` | Alpaca instrument/account-environment host | `https://paper-api.alpaca.markets` |
| `ALPACA_DATA_URL` | Alpaca market-data host | `https://data.alpaca.markets` |
| `ALPACA_FEED` | Alpaca equity feed | `iex` |
| `QUOTE_ASSETS` | Binance quote-asset filter | `USDT,USDC` |
| `LLM_PROVIDER` | Background/default model mode | `mock` / `openai_compat` |
| `LLM_BASE_URL` | OpenAI-compatible API base URL | `https://api.deepseek.com/v1` in the example |
| `LLM_MODEL` | Provider model identifier | `deepseek-chat` in the example |
| `LLM_API_KEY` | Backend model credential | Replace the example placeholder for real calls |
| `ANALYZE_EVERY` | Analyze every Nth closed bar | `1`; use a positive integer |
| `DEMO_INTERVAL_S` | Seconds between synthetic candles | `3` |

Supported intervals: `1m`, `5m`, `15m`, `1h`, `4h`, `1d`.

Compose passes settings explicitly to each service. Adding an arbitrary variable to `.env` does not make it available inside a container. [docker-compose.yml](../docker-compose.yml) is the reference for which settings reach which services.

## Automatic analysis

With `LLM_PROVIDER=mock`, background analyses use the mock model. A key submitted from the browser uses the real provider for that request without changing the background analyzer. Later background results can replace a manually requested result.

To use a real model in the background, configure:

```dotenv
LLM_PROVIDER=openai_compat
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL=deepseek-chat
LLM_API_KEY=replace_with_your_provider_key
ANALYZE_EVERY=5
```

Use your provider's actual base URL, model, and key. This example analyzes every fifth closed bar. The default `ANALYZE_EVERY=1` analyzes every closed bar.

**Background calls continue while the backend is running, even with the browser closed.** Each full analysis has two model stages and may retry. Follow-up requests and replays can add calls. To stop the backend, run `docker compose stop` from the project folder.

The browser's daily demo allowance applies to certain manual requests using the backend key. It is not a spending cap for the background analyzer or replay. `ANALYZE_EVERY` controls frequency, not a monetary budget.

## API keys and storage

There are two different ways to supply a model key:

| Method | Where it is kept | How it is used |
| --- | --- | --- |
| Browser key | Memory by default; optionally browser local storage | Sent to your backend in a request header, then used to call the configured provider |
| Backend key | Your `.env` file and container environment | Used by background analysis and requests using backend credentials |

A browser-supplied key **does reach the backend**. That path is designed to avoid writing it to application logs, the database, or the message bus. It is not a browser-to-provider connection.

Remembering the browser key persists it on that browser profile. Leaving the option off keeps it in memory until reload. A backend key is deliberately stored in local configuration; the browser-key storage guarantee does not apply to it.

Do not commit `.env`. The repository ignores the project and terminal `.env` files.

Use `localhost` for this local setup. Browser keys are accepted over loopback HTTP; non-loopback requests require HTTPS unless explicitly overridden. Keep `ALLOW_INSECURE_KEY_HEADER=false` and `TRUST_PROXY_HEADERS=false` for this guide.

## Frontend connection

| Setting in `terminal/.env` | Purpose | Default |
| --- | --- | --- |
| `VITE_API_URL` | Backend address | `http://localhost:8000` |
| `VITE_DEFAULT_SYMBOL` | Preferred initial symbol, if available | `AAPL` |

Do not put provider secrets in `VITE_` variables: these values are exposed to browser code.

If you change the frontend port or origin, also update the backend's `CORS_ORIGINS`. The supplied defaults allow the interface at ports 5174 and 4173 on localhost and 127.0.0.1.

## Data availability

Alpaca is registered only when both credential fields are populated. Binance remains a registered public-data source. A missing or restricted source can leave a partial catalogue.

Alpaca equity backfills are bounded in the implementation; larger intraday intervals can return fewer than the requested 200 bars. Read short-history messages in the app instead of assuming every interval has equal coverage.

Synthetic bars and real bars are marked separately. Switching `INGEST_MODE` changes which data the services read; it does not convert old synthetic candles into real data.
