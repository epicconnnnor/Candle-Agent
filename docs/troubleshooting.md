# Troubleshooting

[Home](../README.md) · [Installation](installation.md) · [Configuration](configuration.md)

Run Docker commands from the project folder, where `docker-compose.yml` lives. Run npm commands from `terminal/`.

## The backend does not start

Make sure Docker Desktop or Docker Engine is running, then check:

```text
docker info
docker compose ps -a
docker compose logs --tail=50 nats api ingest analyzer
```

The backend is ready when [http://localhost:8000/healthz](http://localhost:8000/healthz) returns `{"status":"ok"}`.

A Docker daemon connection error means Docker itself is unavailable. An exited service or repeated restarts needs its service log inspected. Remove credentials from any log excerpt before sharing it.

## The browser cannot connect

Keep `npm run dev` running in `terminal/`, then open [http://localhost:5174](http://localhost:5174).

If the backend health check works but the interface does not connect, check `VITE_API_URL` and `CORS_ORIGINS` in the [configuration reference](configuration.md#frontend-connection). Restart the frontend after changing its environment file.

Port 8000 serves a separate basic chart page; the main interface is at 5174. If Vite reports a different port because 5174 is occupied, close the other process if it is yours, or configure that new origin explicitly.

## PowerShell says npm.ps1 cannot run

Use the Windows executable wrapper:

```powershell
npm.cmd ci
npm.cmd run dev
```

You do not need to change your machine's execution policy for these commands.

## A symbol warning appears during the no-key demo

The symbol catalogue queries real providers even in synthetic demo mode. Without Alpaca keys, stocks may be absent; Binance may also be unreachable or restricted.

Use **Load a stored example** to explore the included AAPL, MSFT, or TSLA analysis. These files do not need the market-data catalogue. If the example buttons are also missing, check that the backend is reachable.

## I added keys, but prices or answers still look artificial

Credentials alone do not change modes.

For real candles, set `INGEST_MODE=live`. For real background model output, set `LLM_PROVIDER=openai_compat` and provide valid model settings. A browser key can make a real manual model request while background analysis remains mock.

Apply backend changes with:

```text
docker compose up -d
```

A plain restart reuses the previous environment.

## Alpaca is missing, or returns an authentication error

Set both `ALPACA_KEY_ID` and `ALPACA_SECRET_KEY`. Confirm the credentials match the configured Alpaca environment and have access to the requested data.

Keep these URLs distinct:

```dotenv
ALPACA_BASE_URL=https://paper-api.alpaca.markets
ALPACA_DATA_URL=https://data.alpaca.markets
```

Do not add `/v2`; the application adds API paths itself.

## Binance reports region_blocked or HTTP 451

The provider is restricting access from your connection. Use another supported source you can access. Retrying the same blocked connection is not an installation fix.

## Candles are missing or not updating

Exit a stored example first: its chart is intentionally frozen.

For real data, verify the selected symbol, interval, source credentials, and market hours. Stocks may have no new candles outside their trading session. A short history at a larger interval can be a source/backfill limit.

Check the latest candle and analysis timestamps separately. A working connection does not imply a new candle has closed or that analysis has finished.

## The API-key test or analysis fails

The key must match the provider configured by `LLM_BASE_URL` and `LLM_MODEL`. The model controls in Settings do not currently update those backend values.

Use **Test key** after entering a key. Check provider access, account credit, and the exact model identifier. Rate-limit errors need time to clear; changing the browser display does not reset them.

Use the default localhost addresses for local key requests. An insecure-connection error on a LAN address is not fixed by changing the model.

## Stop did not stop a model call

The Analyze button's **Stop** action ends the interface's waiting state, not the backend request. Closing the browser also leaves automatic analysis running.

Use `docker compose stop` when you want to stop the backend. Calls already sent to a provider may still be billed.

## My updates did not take effect

After editing backend `.env`:

```text
docker compose up -d
```

After pulling application changes:

```text
docker compose up -d --build
```

After frontend dependency changes, run `npm ci` in `terminal/` and restart `npm run dev`.

## Keep your saved data

The database lives in the `agent-data` Docker volume. Normal `docker compose stop` and `docker compose down` preserve that volume. Adding `-v` to `down` removes volumes and can delete your stored data; it is not part of the normal update procedure.
