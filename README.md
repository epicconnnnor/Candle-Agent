# Candle Agent

A price-action analysis assistant for traders who want to inspect market structure, review possible setups, and ask questions about the reasoning.

Run it on your own computer and use it in your browser. Candle Agent reads structured candle data, produces a market diagnosis, and explains a possible trade setup or a no-trade decision. It does not place orders.

![Candle Agent showing a chart, market diagnosis, support and resistance levels, and follow-up panel](assets/Candle-Agent.png)

**[Install locally](docs/installation.md)** · [User guide](docs/user-guide.md) · [Technical guide](docs/technical-guide.md)

## What you can do

- **Read the market:** inspect trends, ranges, market cycles, and key price levels.
- **Review a setup:** see the entry, stop, target, decision path, and explanation when a setup is returned.
- **Ask follow-up questions:** discuss a completed analysis and what would invalidate it.
- **Explore saved examples:** view included AAPL, MSFT, and TSLA analyses without API keys or model charges.
- **Evaluate the output:** replay stored history and score analyses against subsequent price action.

Analysis has two stages: first the model describes the market, then it uses that diagnosis to assess a setup. A no-trade result is a normal outcome.

## Get started

Start with the **[local installation guide](docs/installation.md)**. It walks through Windows PowerShell and macOS/Linux setup, then opens a saved example so you can explore the interface before configuring any accounts.

You need Git, Docker with Compose v2, and Node.js with npm. Python runs inside Docker; a separate Python installation is only needed for backend development.

There is no hosted service to sign up for in this guide. You start the backend and browser interface on your computer. Optional real market data and model calls require internet access and the relevant provider credentials.

After installation:

1. Load a saved example and inspect its diagnosis and price levels.
2. Follow the optional setup for real market data and a model provider.
3. Select a symbol and timeframe, click **Analyze**, and read the result.
4. Use **Follow-up** to ask about the completed analysis.

See the [user guide](docs/user-guide.md) for the controls and their current limitations.

## Markets and data

| Source | Coverage | Credentials |
| --- | --- | --- |
| Alpaca | US stocks and crypto | Alpaca key ID and secret |
| Binance | Crypto pairs, where the service is accessible | No market-data key |
| Saved examples | Included historical AAPL, MSFT, and TSLA analyses | None |
| Synthetic demo | Generated candles with a mock model for testing | None |

Supported intervals are 1m, 5m, 15m, 1h, 4h, and 1d. Availability and history depend on the source. Binance can return a regional restriction, including from US connections.

## Limitations and evaluation

Candle Agent is experimental. A structured explanation is not evidence that a setup will succeed.

In the reported small AAPL 1-minute evaluation, the model did not beat the majority-class baseline for regime and cycle classification. Repeating analyses also changed the results. These findings do not establish performance across other markets or timeframes, and the trade sample is too small to establish profitability.

The [evaluation results](docs/results.md) retain the measurements and caveats. The [scoring design](docs/scoring-design.md) explains baselines, overlapping samples, and when the scorer withholds a result.

## Documentation

| Guide | What it covers |
| --- | --- |
| [Installation](docs/installation.md) | Install, launch, configure real data, stop, and update |
| [User guide](docs/user-guide.md) | First analysis, chart controls, decisions, and follow-up |
| [Configuration](docs/configuration.md) | Data sources, model settings, and API-key handling |
| [Troubleshooting](docs/troubleshooting.md) | Connection, setup, data, and analysis problems |
| [Technical guide](docs/technical-guide.md) | Repository map, services, API, replay, and scoring |
| [Architecture](docs/architecture.md) | Design decisions and engineering tradeoffs |
| [Contributing](CONTRIBUTING.md) | Development setup and checks |

## License

[MIT](LICENSE).

For learning and research. Outputs are not investment advice; trading decisions remain your own.
