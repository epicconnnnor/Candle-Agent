/**
 * Types mirroring the backend's actual response shapes.
 *
 * Sources of truth, kept in this order:
 *   candle_agent/schemas.py            Stage1 / Stage2
 *   candle_agent/services/api.py       route payloads
 *   candle_agent/services/ingest.py    ingest.status.* events
 *   candle_agent/db.py                 stored bar and analysis rows
 *
 * Note these are the REAL shapes, which differ from the old mock: stage 1
 * carries `key_levels` and `summary` (not bias/cycle/ema_state), and every
 * stage 2 price is nullable because `no_trade` is a valid answer.
 */

// --- primitives (schemas.py) -------------------------------------------

export type Regime = "bull_trend" | "bear_trend" | "range" | "chop";
export type Strength = "weak" | "moderate" | "strong";
export type Confidence = "low" | "medium" | "high";
export type Decision =
  | "buy_limit"
  | "sell_limit"
  | "buy_stop"
  | "sell_stop"
  | "market_buy"
  | "market_sell"
  | "no_trade";

export type AssetClass = "crypto" | "equity";
export type Interval = "1m" | "5m" | "15m" | "1h" | "4h" | "1d";

export interface Stage1 {
  regime: Regime;
  strength: Strength;
  key_levels: number[];
  summary: string;
}

export interface Stage2 {
  decision: Decision;
  entry: number | null;
  stop: number | null;
  target: number | null;
  risk_reward: number | null;
  confidence: Confidence;
  reasoning_chain: string[];
}

// --- bars ---------------------------------------------------------------

/** A bar row as stored and served: `ts` is epoch MILLISECONDS. */
export interface BarRow {
  symbol: string;
  interval: string;
  ts: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

/** A bar in chart form: `time` is epoch SECONDS (lightweight-charts). */
export interface Bar {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

// --- GET /symbols -------------------------------------------------------

export interface SymbolInfo {
  symbol: string;
  name: string;
  asset_class: AssetClass;
  source: string;
  /** Source-specific extras flattened in by SymbolInfo.as_dict(). */
  baseAsset?: string;
  quoteAsset?: string;
  exchange?: string | null;
}

export interface SymbolsResponse {
  symbols: SymbolInfo[];
  sources: string[];
  intervals: Interval[];
  /** source name -> why it could not be reached. Empty when all answered. */
  unavailable: Record<string, string>;
}

// --- POST /subscribe ----------------------------------------------------

export interface SubscribeRequest {
  symbol: string;
  interval?: string;
  source?: string;
}

export interface SubscribeResponse {
  status: "ok";
  changed: boolean;
  source: string;
  symbol: string;
  interval: string;
  mode: string;
  /** Ingest's last status event; `{}` before anything has happened. */
  state: IngestStatus | Record<string, never>;
  asset_class: AssetClass;
  bars: BarRow[];
}

// --- analysis -----------------------------------------------------------

/** GET /api/analysis/{symbol} - a stored row, so it carries id/interval. */
export interface StoredAnalysis {
  id: number;
  symbol: string;
  ts: number;
  interval: string;
  stage1: Stage1;
  stage2: Stage2;
  model: string;
  latency_ms: number;
  /** Last close when the analysis was produced. Null on pre-migration rows. */
  price_at: number | null;
  /** ATR14 at that moment. Null on pre-migration rows. */
  atr_at: number | null;
}

/** POST /api/analyze/{symbol} - 202, the result arrives over SSE. */
export interface AnalyzeQueued {
  status: "queued";
  symbol: string;
}

/** POST /api/analyze/{symbol} with X-LLM-Key - runs inline, returns 200. */
export interface InlineAnalysis {
  status: "completed";
  symbol: string;
  key_source: "user";
  stage1: Stage1;
  stage2: Stage2;
  model: string;
  latency_ms: number;
}

/** POST /api/llm/test */
export interface KeyTestResult {
  valid: boolean;
  model: string;
  detail: string;
}

// --- SSE (GET /api/events) ---------------------------------------------

/** analysis.completed.<SYMBOL> */
export interface AnalysisCompleted {
  symbol: string;
  bar_ts: number;
  stage1: Stage1;
  stage2: Stage2;
  model: string;
  latency_ms: number;
}

/** snapshot.built.<SYMBOL> - the feature packet is ready. */
export interface SnapshotBuilt {
  symbol: string;
  interval: string;
  bars: number;
  first_ts: number;
  last_ts: number;
}

/** analysis.stage1.completed.<SYMBOL> - diagnosis validated, before stage 2. */
export interface Stage1Completed {
  symbol: string;
  interval: string;
  bar_ts: number;
  stage1: Stage1;
}

/** bars.closed.<SYMBOL> - the bar dict with `symbol` merged in. */
export interface BarClosed {
  symbol: string;
  ts: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

/** paper.update.<SYMBOL> */
export interface PaperUpdate {
  symbol: string;
  active: Record<string, unknown> | null;
  summary: Record<string, unknown>;
}

/**
 * ingest.status.<SYMBOL>. `state` is the lifecycle; `kind` classifies a
 * failure (region_blocked, unknown_symbol, auth, closed, unavailable,
 * stalled, reconnecting) and is what distinguishes a 451 from a bad symbol.
 */
export type IngestState =
  | "connecting"
  | "connected"
  | "streaming"
  | "stopped"
  | "ended"
  | "failed"
  | "error"
  | "unhealthy"
  | "stalled"
  | "backfilled"
  | "backfill_failed"
  | "market_closed"
  | "market_unknown";

export type StatusKind =
  | "region_blocked"
  | "unknown_symbol"
  | "auth"
  | "closed"
  | "unavailable"
  | "stalled"
  | "reconnecting"
  | "error";

export interface IngestStatus {
  ts: number;
  mode: string;
  state: IngestState;
  symbol?: string;
  interval?: string;
  source?: string;
  kind?: StatusKind;
  code?: number | string | null;
  reason?: string | null;
  message?: string;
  retryable?: boolean;
  // backfilled
  bars?: number;
  requested?: number;
  partial?: boolean;
  // market_closed
  next_open?: string | null;
  next_close?: string | null;
  // unhealthy / stalled
  attempts?: number;
  retry_in_s?: number;
  quiet_for_s?: number;
}

/** The SSE envelope the api wraps every bus message in. */
export interface SseEnvelope {
  subject: string;
  data: unknown;
}

export type ConnectionState = "connecting" | "connected" | "reconnecting" | "disconnected";
