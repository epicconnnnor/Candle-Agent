# Using Candle Agent

[Home](../README.md) · [Installation](installation.md) · [Configuration](configuration.md)

Open [http://localhost:5174](http://localhost:5174) after starting the backend and interface.

## Explore a saved example

Click one of the AAPL, MSFT, or TSLA buttons in the example bar. Each loads historical candles and a saved analysis without making a model request.

Start with the market diagnosis, then inspect the chart levels and decision explanation. Some examples return no trade; entry, stop, and target values only apply when the model returns a setup.

While an example is open, its banner identifies the historical timestamp and the chart is frozen. **Back to live** exits the example and restores the previous selection; it does not change your backend from synthetic data to real data. Follow the [installation guide](installation.md#7-optional-connect-real-market-data) to configure a real feed.

Do not use Analyze or Follow-up to discuss a frozen example. Those requests use backend analysis state rather than sending the displayed example as their context.

## Run an analysis on your feed

1. Configure a data source and model using the [installation guide](installation.md).
2. Exit any saved example.
3. Choose a symbol from the top-left picker and select a timeframe.
4. Wait for candles to load. Check the source, connection status, and latest update time.
5. Click **Analyze** and wait for the diagnosis and decision.
6. Read the explanation alongside the chart, including any no-trade conclusion.

The default installation uses a mock model. To get actual model output, supply a browser key for the configured provider or configure the backend model credentials.

**Stop** resets the Analyze button's waiting state. It does not cancel an in-progress backend model call or its charges.

## Read the result

| Area | What to look for |
| --- | --- |
| Pipeline | Progress through data, snapshot, diagnosis, and decision |
| Market summary | Regime, cycle, strength, and key levels |
| Chart and Levels | Support, resistance, and setup prices where available |
| Stage 1 diagnosis | The model's description of the market |
| Decision | A setup or a no-trade result |
| Decision path and reasoning | The checks and explanation behind the decision |
| Session | Source, connection, and update information |
| Follow-up | Questions about a completed backend analysis |

Stage 1 describes market structure. Stage 2 uses that diagnosis to choose a setup or abstain. Confidence labels are model output, not a measured probability of profit.

A connected status does not mean an analysis is current. Check its timestamp, symbol, and timeframe against the chart. A chart can keep updating while the displayed analysis refers to an earlier candle.

## Ask a follow-up question

After completing an analysis on the current feed, use **Follow-up**. Useful questions include:

- Which levels matter most in this diagnosis?
- What would invalidate this setup?
- Why did this analysis return no trade?

Follow-up requests can incur model charges. The visible chat history is held in the current browser session; do not treat it as a durable journal. Keep questions tied to the current analysis, and start a fresh conversation when changing context.

## Settings that currently apply

The API-key controls support entering, testing, optionally remembering, and forgetting a key. The timezone control changes displayed times; stored timestamps remain in UTC.

Other displayed controls, including provider, model, temperature, maximum tokens, analysis frequency, risk, and notifications, do not currently apply those settings to the backend. Use the [configuration reference](configuration.md) for supported environment settings. There is no working notification setup described by this guide.

## Understand the three data modes

| Mode | What appears | Model use |
| --- | --- | --- |
| Saved example | A frozen historical chart and saved result | None when loading |
| Synthetic demo | Generated candles for testing | Mock by default |
| Real feed | Candles from Alpaca or Binance | Mock or real, depending on configuration |

The label **Back to live** means leaving the saved example. Always check your configured data source before interpreting the resulting chart as market prices.

## Where to go next

See [troubleshooting](troubleshooting.md) for missing candles or failed analyses. For historical experiments, use the [technical guide](technical-guide.md#replay-and-score-history) and read the [evaluation limitations](results.md).
