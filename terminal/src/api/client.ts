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
  ChatReply,
  ChatTurn,
  ConnectionState,
  DemoSample,
  DemoSampleSummary,
  DemoStatus,
  IngestStatus,
  InlineAnalysis,
  KeyTestResult,
  PaperUpdate,
  SnapshotBuilt,
  SseEnvelope,
  Stage1Completed,
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

/**
 * Attach a visitor's LLM key to one request.
 *
 * A header, never a query string: URLs land in browser history, proxy
 * logs and server access logs.
 *
 * Where the key rests is the user's choice and is made in Settings: React
 * state only by default, or this browser's localStorage if they opt in.
 * See apiKeyStorage.ts. Neither is a server record - the key is sent with
 * the requests that need it and is never persisted server-side.
 */
const keyHeader = (key?: string | null): Record<string, string> =>
  key ? { "X-LLM-Key": key } : {};

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

/**
 * Latest stored analysis, scoped to an interval when given.
 *
 * 404 before the first analysis completes; null rather than throwing.
 */
export const getAnalysis = async (
  symbol: string,
  interval?: string,
): Promise<StoredAnalysis | null> => {
  const q = interval ? `?interval=${encodeURIComponent(interval)}` : "";
  try {
    return await request<StoredAnalysis>(
      `/api/analysis/${encodeURIComponent(symbol)}${q}`);
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) return null;
    throw e;
  }
};

/**
 * Queue or run an analysis.
 *
 * Without a key: 202, the analyzer picks it up and the result arrives over
 * SSE. With a visitor key the backend runs it inline and returns the
 * finished analysis, because a key must never ride the persisted bus.
 */
export const requestAnalysis = (symbol: string, apiKey?: string | null) =>
  request<AnalyzeQueued | InlineAnalysis>(
    `/api/analyze/${encodeURIComponent(symbol)}`,
    { method: "POST", headers: keyHeader(apiKey) },
  );

/**
 * Ask a follow-up question about the stored analysis.
 *
 * Always inline - there is no queued form. The server trims the history
 * it is sent, so passing the whole visible conversation is safe; it is
 * capped here too so a long session does not ship a large body only to
 * have most of it discarded.
 */
export const askFollowUp = (
  symbol: string,
  message: string,
  history: ChatTurn[],
  apiKey?: string | null,
) =>
  request<ChatReply>(`/api/chat/${encodeURIComponent(symbol)}`, {
    method: "POST",
    headers: keyHeader(apiKey),
    body: JSON.stringify({ message, history: history.slice(-12) }),
  });

/** How many free analyses are left before a key is needed. */
export const getDemoStatus = () => request<DemoStatus>("/api/demo/status");

/** The stored examples this build ships with. */
export const getDemoSamples = () =>
  request<{ samples: DemoSampleSummary[] }>("/api/demo/samples");

/** One stored example in full: bars, diagnosis, decision, checklist. */
export const getDemoSample = (id: string) =>
  request<DemoSample>(`/api/demo/samples/${encodeURIComponent(id)}`);

/** Minimal upstream call to tell a good key from a bad one. */
export const testKey = (apiKey: string) =>
  request<KeyTestResult>("/api/llm/test", {
    method: "POST",
    headers: keyHeader(apiKey),
  });

// --- SSE ----------------------------------------------------------------

export interface FeedHandlers {
  onBar?: (bar: BarClosed) => void;
  onAnalysis?: (a: AnalysisCompleted) => void;
  onStatus?: (s: IngestStatus) => void;
  onSnapshot?: (s: SnapshotBuilt) => void;
  onStage1?: (s: Stage1Completed) => void;
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
      } else if (subject.startsWith("analysis.stage1.completed.")) {
        // checked before analysis.completed: distinct subjects, and this
        // one is the earlier signal
        handlers.onStage1?.(data as Stage1Completed);
      } else if (subject.startsWith("analysis.completed.")) {
        handlers.onAnalysis?.(data as AnalysisCompleted);
      } else if (subject.startsWith("snapshot.built.")) {
        handlers.onSnapshot?.(data as SnapshotBuilt);
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
