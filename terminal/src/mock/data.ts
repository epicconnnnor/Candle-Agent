import type { Analysis, Bar, Stage1, Stage2 } from "../types";

// Placeholder list only - the real one comes from GET /symbols, which
// merges every registered source. ES and NQ used to sit here; they are
// CME futures and no configured source serves them.
export const SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"];

// must stay in step with candle_agent/intervals.py
export const TIMEFRAMES = ["1m", "5m", "15m", "1h", "4h", "1d"];

const TICK = 0.25;
const BAR_SECONDS = 300; // 5m
const COUNT = 160;

const toTick = (n: number) => Math.round(n / TICK) * TICK;

/** Deterministic PRNG so the mock chart is identical on every reload. */
function mulberry32(seed: number) {
  return () => {
    seed |= 0;
    seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Random walk with a mild upward drift, snapped to the tick grid. */
function generate(): Bar[] {
  const rnd = mulberry32(20260825);
  // anchor the series so the newest bar sits at "now", rounded to the bar grid
  const end = Math.floor(Date.now() / 1000 / BAR_SECONDS) * BAR_SECONDS;
  const out: Bar[] = [];
  let price = 5872;

  for (let i = 0; i < COUNT; i++) {
    const drift = 0.22;
    const shock = (rnd() - 0.5) * 6.5;
    const open = price;
    const close = toTick(open + drift + shock);
    const wick = 0.8 + rnd() * 3.2;
    const high = toTick(Math.max(open, close) + rnd() * wick);
    const low = toTick(Math.min(open, close) - rnd() * wick);
    out.push({
      time: end - (COUNT - 1 - i) * BAR_SECONDS,
      open: toTick(open),
      high,
      low,
      close,
      volume: Math.round(8000 + rnd() * 16000),
    });
    price = close;
  }
  return out;
}

export const initialBars: Bar[] = generate();

/** Advances the series by one bar. Used by Incremental and the Live toggle. */
export function nextBar(prev: Bar, seed = Date.now()): Bar {
  const rnd = mulberry32(seed);
  const open = prev.close;
  const close = toTick(open + (rnd() - 0.48) * 6);
  const wick = 0.8 + rnd() * 3;
  return {
    time: prev.time + BAR_SECONDS,
    open,
    high: toTick(Math.max(open, close) + rnd() * wick),
    low: toTick(Math.min(open, close) - rnd() * wick),
    close,
    volume: Math.round(8000 + rnd() * 16000),
  };
}

/* ---- levels derived from the generated series, so price lines stay on-chart ---- */

const recent = initialBars.slice(-56);
const support = toTick(Math.min(...recent.map((b) => b.low)));
const resistance = toTick(Math.max(...recent.map((b) => b.high)));
const lastClose = initialBars[initialBars.length - 1].close;

const stage1: Stage1 = {
  regime: "bull_trend",
  strength: "moderate",
  bias: "bull",
  diagnosis:
    "Higher lows into a tightening range beneath prior resistance, with the 20 EMA holding as dynamic support on every retest.",
  cycle: "IMPULSE 3",
  hl_count: { highs: 4, lows: 3 },
  ema_state: "20 > 50 > 200",
  confidence: "medium",
  support: [support, toTick(support - 6.5)],
  resistance: [resistance, toTick(resistance + 7.25)],
};

const entry = toTick(lastClose - 2.5);
const stop = toTick(support - 1.25);
const target = toTick(entry + (entry - stop) * 2);

const stage2: Stage2 = {
  decision: "buy_limit",
  entry,
  stop,
  target,
  risk_reward: Math.round(((target - entry) / (entry - stop)) * 10) / 10,
  confidence: "medium",
  reasoning_chain: [
    "Trend structure intact: four higher lows with no lower high printed.",
    "Pullback is landing on the 20 EMA, which has held three prior retests.",
    "Volume contracted through the pullback and expanded on the last impulse.",
    "Risk is defined below the swing low at " + stop.toFixed(2) + ".",
    "Target set at 2R, just under the next resistance shelf.",
  ],
  reasons: {
    entry: "Limit at the 20 EMA retest, inside the value area.",
    stop: "Below the most recent swing low and the session support shelf.",
    target: "2R, placed just beneath the next resistance band.",
  },
};

export const analysis: Analysis = {
  symbol: "BTCUSDT",
  timeframe: "5m",
  ts: Date.now(),
  stage1,
  stage2,
  model: "deepseek-chat",
  latency_ms: 1840,
};

/** Wilder ATR over the closing series. */
export function atr(bars: Bar[], period = 14): number {
  if (bars.length < 2) return 0;
  const trs = bars.slice(1).map((b, i) => {
    const p = bars[i];
    return Math.max(b.high - b.low, Math.abs(b.high - p.close), Math.abs(b.low - p.close));
  });
  const window = trs.slice(-period);
  return window.reduce((a, b) => a + b, 0) / window.length;
}
