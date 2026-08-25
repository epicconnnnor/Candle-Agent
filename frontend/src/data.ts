/**
 * Hardcoded mock data.
 *
 * Shapes mirror the analyzer's two-stage output (see candle_agent/schemas.py):
 * stage1 = market diagnosis, stage2 = schema-validated trade decision.
 * Swap this module for a fetch of /api/analysis/{symbol} + /api/bars/{symbol}.
 */

export interface Bar {
  ts: number; // epoch ms, bar close
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export type Regime = "bull_trend" | "bear_trend" | "range" | "chop";
export type Strength = "weak" | "moderate" | "strong";
export type Decision = "buy_limit" | "sell_limit" | "buy_stop" | "sell_stop" | "flat";
export type Confidence = "low" | "medium" | "high";

/** Stage 1 — what the market is doing. */
export interface Stage1 {
  regime: Regime;
  strength: Strength;
  key_levels: number[];
  summary: string;
}

/** Stage 2 — what to do about it. */
export interface Stage2 {
  decision: Decision;
  entry: number;
  stop: number;
  target: number;
  risk_reward: number;
  confidence: Confidence;
  reasoning_chain: string[];
}

export interface Analysis {
  symbol: string;
  timeframe: string;
  ts: number;
  stage1: Stage1;
  stage2: Stage2;
  model: string;
  latency_ms: number;
}

/** One of the three read-outs below the fold. */
export interface Factor {
  key: string;
  headline: string;
  detail: string;
  readings: { label: string; value: string }[];
}

export const analysis: Analysis = {
  symbol: "ES",
  timeframe: "5M",
  ts: 1787684700000,
  stage1: {
    regime: "bull_trend",
    strength: "moderate",
    key_levels: [5893.5, 5906.75],
    summary:
      "Higher lows since the 13:15 flush, with price holding the 20-period mean on every retest. Sellers are present at the 5906.75 shelf but have not produced a lower high.",
  },
  stage2: {
    decision: "buy_limit",
    entry: 5899.0,
    stop: 5893.5,
    target: 5910.0,
    risk_reward: 2.0,
    confidence: "medium",
    reasoning_chain: [
      "Pullback toward the 20-period mean inside an intact bull trend.",
      "Prior breakout level at 5893.50 held on the retest and now sits below entry.",
      "Volume expanded on the 13:55 push and contracted into the pullback.",
    ],
  },
  model: "deepseek-chat",
  latency_ms: 1840,
};

export const factors: Factor[] = [
  {
    key: "trend",
    headline: "Bull, moderate",
    detail:
      "Four consecutive higher lows. Slope is positive but flattening into the afternoon session.",
    readings: [
      { label: "Direction", value: "UP" },
      { label: "Slope", value: "+0.42/BAR" },
      { label: "Higher lows", value: "4" },
    ],
  },
  {
    key: "structure",
    headline: "Range top retest",
    detail:
      "Price is pressing the 5906.75 shelf for a third time. Support has migrated up to 5893.50.",
    readings: [
      { label: "Resistance", value: "5906.75" },
      { label: "Support", value: "5893.50" },
      { label: "Tests", value: "3" },
    ],
  },
  {
    key: "volatility",
    headline: "Compressing",
    detail:
      "True range has narrowed for three bars running, typically a precursor to expansion.",
    readings: [
      { label: "ATR(14)", value: "4.10" },
      { label: "Bar range", value: "4.25" },
      { label: "Percentile", value: "28TH" },
    ],
  },
];

/** Most recent bars, oldest first. */
export const bars: Bar[] = [
  { ts: 1787681400000, open: 5891.5, high: 5894.25, low: 5890.75, close: 5893.75, volume: 12480 },
  { ts: 1787681700000, open: 5893.75, high: 5896.0, low: 5892.25, close: 5895.25, volume: 10932 },
  { ts: 1787682000000, open: 5895.25, high: 5895.75, low: 5891.0, close: 5891.75, volume: 14201 },
  { ts: 1787682300000, open: 5891.75, high: 5893.5, low: 5889.25, close: 5892.75, volume: 11760 },
  { ts: 1787682600000, open: 5892.75, high: 5898.25, low: 5892.5, close: 5897.5, volume: 16845 },
  { ts: 1787682900000, open: 5897.5, high: 5901.0, low: 5896.75, close: 5900.25, volume: 18320 },
  { ts: 1787683200000, open: 5900.25, high: 5903.75, low: 5899.5, close: 5902.0, volume: 21094 },
  { ts: 1787683500000, open: 5902.0, high: 5902.5, low: 5897.25, close: 5898.0, volume: 15673 },
  { ts: 1787683800000, open: 5898.0, high: 5899.75, low: 5895.5, close: 5896.25, volume: 13418 },
  { ts: 1787684100000, open: 5896.25, high: 5900.5, low: 5896.0, close: 5899.75, volume: 12087 },
  { ts: 1787684400000, open: 5899.75, high: 5904.25, low: 5899.25, close: 5903.5, volume: 19640 },
  { ts: 1787684700000, open: 5903.5, high: 5906.75, low: 5902.75, close: 5905.0, volume: 22315 },
  { ts: 1787685000000, open: 5905.0, high: 5905.5, low: 5900.25, close: 5901.5, volume: 17206 },
  { ts: 1787685300000, open: 5901.5, high: 5903.25, low: 5899.0, close: 5902.75, volume: 11894 },
];

/* ---------- formatting helpers (UTC, so output is deterministic) ---------- */

export const fmtPrice = (n: number) => n.toFixed(2);

export const fmtVolume = (n: number) => n.toLocaleString("en-US");

export const fmtTime = (ts: number) => {
  const d = new Date(ts);
  const hh = String(d.getUTCHours()).padStart(2, "0");
  const mm = String(d.getUTCMinutes()).padStart(2, "0");
  return `${hh}:${mm}`;
};

export const fmtStamp = (ts: number) => {
  const d = new Date(ts);
  const mon = d.toLocaleString("en-US", { month: "short", timeZone: "UTC" }).toUpperCase();
  return `${mon} ${String(d.getUTCDate()).padStart(2, "0")} ${fmtTime(ts)} UTC`;
};

/** Underscored enum values read better as prose in a print setting. */
export const humanize = (s: string) => s.replace(/_/g, " ");
