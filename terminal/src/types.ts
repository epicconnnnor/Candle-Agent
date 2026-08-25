/** Types mirroring the analyzer's two-stage output (candle_agent/schemas.py). */

export type Regime = "bull_trend" | "bear_trend" | "range" | "chop";
export type Strength = "weak" | "moderate" | "strong";
export type Confidence = "low" | "medium" | "high";
export type Bias = "bull" | "bear";
export type Decision =
  | "buy_limit"
  | "sell_limit"
  | "buy_stop"
  | "sell_stop"
  | "flat";

/** OHLCV bar. `time` is a UTC timestamp in SECONDS (lightweight-charts format). */
export interface Bar {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

/** Stage 1 - market diagnosis. */
export interface Stage1 {
  regime: Regime;
  strength: Strength;
  bias: Bias;
  /** One-sentence diagnosis; rendered as the panel headline. */
  diagnosis: string;
  cycle: string;
  hl_count: { highs: number; lows: number };
  ema_state: string;
  confidence: Confidence;
  support: number[];
  resistance: number[];
}

/** Stage 2 - schema-validated trade decision. */
export interface Stage2 {
  decision: Decision;
  entry: number;
  stop: number;
  target: number;
  risk_reward: number;
  confidence: Confidence;
  reasoning_chain: string[];
  /** One-line justification per level, shown on the three cards. */
  reasons: { entry: string; stop: string; target: string };
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

export interface ChatMessage {
  id: number;
  role: "user" | "agent";
  text: string;
}
