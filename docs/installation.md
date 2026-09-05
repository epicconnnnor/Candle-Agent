# Install Candle Agent locally

[Home](../README.md) · [User guide](user-guide.md) · [Troubleshooting](troubleshooting.md)

This guide runs Candle Agent on your own computer. You will start two parts: the backend in Docker and the browser interface in a terminal window. No cloud account, domain, or public deployment is needed.

Start with the included examples. You can add real market data and a model provider after the app is running.

## 1. Install the prerequisites

| Tool | What you need |
| --- | --- |
| Git | To download and update the repository |
| Docker | Docker Desktop on Windows/macOS, or Docker Engine with Compose v2 on Linux |
| Node.js and npm | Node.js 22 LTS or newer supported LTS; npm is included |

Get the installers from [Git](https://git-scm.com/downloads), [Docker](https://docs.docker.com/get-started/get-docker/), and [Node.js](https://nodejs.org/en/download). On Windows, follow Docker Desktop's WSL 2 setup instructions and use Linux containers.

Open Docker Desktop and wait for its engine to start. On Linux, make sure Docker Engine is running and your user can run Docker commands.

Open **PowerShell** on Windows or **Terminal** on macOS/Linux. Check:

```text
git --version
docker compose version
docker info
node --version
npm --version
```

Each version command should print a version; `docker info` should show server details without a connection error. If Windows PowerShell blocks `npm.ps1`, use `npm.cmd` wherever this guide says `npm`.

You do not need to install Python separately for these steps. Internet access is needed to download the application dependencies and Docker images.

## 2. Download the project

Run these commands on any platform:

```text
git clone https://github.com/epicconnnnor/Candle-Agent.git
cd Candle-Agent
```

Keep this window open. It is now in the **project folder**, the folder containing `docker-compose.yml`.

## 3. Create your local settings

For a first installation, copy the example settings.

**Windows PowerShell**

```powershell
Copy-Item .env.example .env
```

**macOS / Linux**

```bash
cp .env.example .env
```

If you already have a `.env` file, keep it and edit it instead of overwriting it.

Open `.env` in a text editor. For the first run, leave these values as follows:

```dotenv
INGEST_MODE=demo
LLM_PROVIDER=mock
```

No API keys are needed for this first run. The mock model produces test responses, and the synthetic candles are not market prices. The placeholder `LLM_API_KEY=1` in the example file is not a working key; it is unused by the mock model.

## 4. Start the backend

From the project folder:

```text
docker compose up -d --build
docker compose ps
```

The first build may take several minutes. The services `nats`, `ingest`, `analyzer`, `replay`, `paper`, and `api` should be running. Give them a moment to connect.

Open [the backend health check](http://localhost:8000/healthz) in your browser. It should show:

```json
{"status":"ok"}
```

If it does not, see [backend startup problems](troubleshooting.md#the-backend-does-not-start).

## 5. Start the browser interface

In the same terminal window:

```text
cd terminal
npm ci
npm run dev
```

Keep this command running. Open **[http://localhost:5174](http://localhost:5174)**.

Port **5174** is the main interface used throughout these guides. Port **8000** belongs to the backend and also serves a separate basic chart page.

No `terminal/.env` file is needed for the default local setup: the interface already connects to `http://localhost:8000`.

## 6. Open your first example

Near the top of the page, find **New here? Load a stored example** and click **AAPL**, **MSFT**, or **TSLA**.

You should see a chart, a market diagnosis, and the saved decision details. The example banner identifies the historical analysis and says that the display is not live.

Loading an example does not call a model or require market-data credentials. You have finished the basic installation when you can open one.

The symbol picker still tries to load a market-data catalogue in demo mode. Without Alpaca credentials, or where Binance is unavailable, you may see a symbol or connection warning. The saved-example buttons work independently of that catalogue. Use them for this first walkthrough; synthetic demo mode does not provide a complete offline symbol-browsing experience.

Saved examples are for viewing. Configure a current feed and run a new analysis before using follow-up chat. See the [user guide](user-guide.md) next.

## 7. Optional: connect real market data

This still runs on your computer. “Real data” means candles from a provider, not a hosted version of Candle Agent.

First, press **Ctrl+C** in the interface terminal, then return to the project folder:

```text
cd ..
docker compose stop
```

Edit the existing project `.env` and choose **one** source below. Keep `LLM_PROVIDER=mock` initially so you can check the feed without paying for model calls.

### Alpaca: US stocks and crypto

Obtain an Alpaca key ID and secret from your account, then set:

```dotenv
INGEST_MODE=live
DEFAULT_SOURCE=alpaca
SYMBOL=AAPL
INTERVAL=1m
ALPACA_KEY_ID=replace_with_your_key_id
ALPACA_SECRET_KEY=replace_with_your_secret
ALPACA_BASE_URL=https://paper-api.alpaca.markets
ALPACA_DATA_URL=https://data.alpaca.markets
ALPACA_FEED=iex
LLM_PROVIDER=mock
```

Use credentials for the configured Alpaca environment. Keep the two URLs separate and do not append `/v2`. Candle Agent uses these endpoints for market data and instrument information; it does not submit orders.

### Binance: crypto

Where Binance is accessible, set:

```dotenv
INGEST_MODE=live
DEFAULT_SOURCE=binance
SYMBOL=BTCUSDT
INTERVAL=1m
LLM_PROVIDER=mock
```

No Binance API key is needed for public market data. If you want only Binance in the catalogue, leave both Alpaca credential values empty. If Binance returns a regional restriction, use an accessible supported source.

### Apply your source settings

From the project folder:

```text
docker compose up -d
cd terminal
npm run dev
```

Reopen [the interface](http://localhost:5174), leave any saved example using **Back to live**, and select your symbol from the picker. Check that candles load and the source is the one you configured. Stocks may show historical bars without new candles outside market hours.

Changing credentials alone does not enable real data: `INGEST_MODE` must be `live`.

## 8. Optional: use a real model

The simplest first model setup uses a key entered in the browser for manual analysis. Keep the background analyzer on `mock` while learning the interface.

In the project `.env`, set the base URL and model name for your OpenAI-compatible provider. The included example uses:

```dotenv
LLM_PROVIDER=mock
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL=deepseek-chat
```

Use a model supported by your provider account. If you change these settings, run `docker compose up -d` from the project folder to apply them.

In the app, open **Settings**, enter that provider's API key, and use **Test key** to check it. Then select a current feed and click **Analyze**. A browser-supplied key uses the real configured provider even when `LLM_PROVIDER=mock`. Analysis and follow-up requests can incur provider charges.

The provider and model controls displayed in Settings do not currently apply backend configuration. Change the URL and model in `.env`; the browser key must belong to that provider. The default mock background analysis continues and can subsequently replace a manual result when another bar closes.

For continuous real-model analysis, see [automatic analysis](configuration.md#automatic-analysis). Supplying a backend key and setting `LLM_PROVIDER=openai_compat` enables paid background calls as candles arrive, even if the browser is closed.

## Stop and start again

To stop the interface, press **Ctrl+C** in its terminal. Then, from the project folder:

```text
docker compose stop
```

Closing the browser alone does not stop the backend.

Next time, open a terminal in the project folder:

```text
docker compose up -d
cd terminal
npm run dev
```

Your database is kept in a Docker volume. A normal stop preserves it.

## Update an existing installation

Stop the interface and run these commands from the project folder after saving any local work:

```text
git pull --ff-only
docker compose up -d --build
cd terminal
npm ci
npm run dev
```

Keep your existing `.env`; compare it with `.env.example` for new settings.

After editing only `.env`, use `docker compose up -d`. After pulling application changes, include `--build`. A plain `docker compose restart` does not load changed environment settings.

Need help? Start with [troubleshooting](troubleshooting.md).
