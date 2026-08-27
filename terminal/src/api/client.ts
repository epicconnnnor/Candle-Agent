/**
 * Typed wrappers around the candle-agent API.
 *
 * Base URL comes from VITE_API_URL so the same build can point at a local
 * stack or a deployed one; it falls back to the compose port for dev.
 */
import type {
  AnalysisCompleted,
  AnalyzeQueued,
  Bar,
  BarClosed,
  BarRow,
  ConnectionState,
  IngestStatus,
  PaperUpdate,
  SseEnvelope,
  StoredAnalysis,
  SubscribeRequest,
  SubscribeResponse,
  SymbolsResponse,
} from "./types";

export const API_URL = (
  import.meta.env.VITE_API_URL ?? "http://localhost:8000"
).replace(/\/$/, "");

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly detail?: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    });
  } catch (e) {
    // a network-level failure has no status; say so rather than "0"
    throw new ApiError(
      `Cannot reach the API at ${API_URL}. Is the stack running?`,
      0,
      String(e),
    );
  }

  if (!res.ok) {
    // FastAPI puts the useful text in `detail`
    let detail: string | undefined;
    try {
      detail = (await res.json())?.detail;
    } catch {
      detail = undefined;
    }
    throw new ApiError(detail ?? `${res.status} ${res.statusText}`, res.status, detail);
  }
  return (await res.json()) as T;
}

// --- bar conversion -----------------------------------------------------

/** Backend bars are epoch ms; lightweight-charts wants epoch seconds. */
export const toBar = (b: BarRow | BarClosed): Bar => ({
  time: Math.floor(b.ts / 1000),
  open: b.open,
  high: b.high,
  low: b.low,
  close: b.close,
  volume: b.volume,
});

// --- routes -------------------------------------------------------------

export const getSymbols = () => request<SymbolsResponse>("/symbols");

/** `state` is `{}` until ingest has published something; narrow before use. */
export const hasStatus = (
  state: SubscribeResponse["state"],
): state is IngestStatus => typeof (state as IngestStatus).state === "string";

export const subscribe = (body: SubscribeRequest) =>
  request<SubscribeResponse>("/subscribe", {
    method: "POST",
    body: JSON.stringify(body),
  });

export const getBars = (symbol: string, limit = 200) =>
  request<BarRow[]>(`/api/bars/${encodeURIComponent(symbol)}?limit=${limit}`);

/** 404 before the first analysis completes; null rather than throwing. */
export const getAnalysis = async (symbol: string): Promise<StoredAnalysis | null> => {
  try {
    return await request<StoredAnalysis>(`/api/analysis/${encodeURIComponent(symbol)}`);
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) return null;
    throw e;
  }
};

/** 202: the analyzer picks the request up, the result arrives over SSE. */
export const requestAnalysis = (symbol: string) =>
  request<AnalyzeQueued>(`/api/analyze/${encodeURIComponent(symbol)}`, { method: "POST" });

// --- SSE ----------------------------------------------------------------

export interface FeedHandlers {
  onBar?: (bar: BarClosed) => void;
  onAnalysis?: (a: AnalysisCompleted) => void;
  onStatus?: (s: IngestStatus) => void;
  onPaper?: (p: PaperUpdate) => void;
  onConnection?: (state: ConnectionState) => void;
}

const RECONNECT_BASE_MS = 1000;
const RECONNECT_CAP_MS = 30000;

/**
 * Subscribe to /api/events, routing by subject.
 *
 * EventSource retries on its own but on a fixed schedule and without
 * telling anyone, so it is driven manually: closed on error, reopened with
 * exponential backoff plus jitter, and every transition reported so the
 * top bar can show what is actually happening.
 *
 * Returns a disposer.
 */
export function openFeed(handlers: FeedHandlers): () => void {
  let source: EventSource | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let attempt = 0;
  let closed = false;

  const connect = () => {
    if (closed) return;
    handlers.onConnection?.(attempt === 0 ? "connecting" : "reconnecting");

    const es = new EventSource(`${API_URL}/api/events`);
    source = es;

    es.onopen = () => {
      attempt = 0;                     // only a real open resets the backoff
      handlers.onConnection?.("connected");
    };

    es.onmessage = (event) => {
      let envelope: SseEnvelope;
      try {
        envelope = JSON.parse(event.data);
      } catch {
        return;                        // keepalive comment or malformed frame
      }
      const { subject, data } = envelope;
      if (subject.startsWith("bars.closed.")) {
        handlers.onBar?.(data as BarClosed);
      } else if (subject.startsWith("analysis.completed.")) {
        handlers.onAnalysis?.(data as AnalysisCompleted);
      } else if (subject.startsWith("ingest.status.")) {
        handlers.onStatus?.(data as IngestStatus);
      } else if (subject.startsWith("paper.update.")) {
        handlers.onPaper?.(data as PaperUpdate);
      }
    };

    es.onerror = () => {
      es.close();
      source = null;
      if (closed) return;
      attempt += 1;
      const ceiling = Math.min(RECONNECT_CAP_MS, RECONNECT_BASE_MS * 2 ** attempt);
      const delay = Math.random() * ceiling;      // full jitter
      handlers.onConnection?.(attempt > 2 ? "disconnected" : "reconnecting");
      timer = setTimeout(connect, delay);
    };
  };

  connect();

  return () => {
    closed = true;
    if (timer) clearTimeout(timer);
    source?.close();
    handlers.onConnection?.("disconnected");
  };
}
