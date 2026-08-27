import { useCallback, useEffect, useRef, useState } from "react";
import { openFeed, toBar } from "../api/client";
import type {
  AnalysisCompleted,
  Bar,
  BarRow,
  ConnectionState,
  IngestStatus,
} from "../api/types";

/** Status slots, kept apart so a market-closed note and a partial backfill
 *  can both be on screen without one overwriting the other. */
export interface Notices {
  problem: IngestStatus | null;
  market: IngestStatus | null;
  backfill: IngestStatus | null;
}

const EMPTY: Notices = { problem: null, market: null, backfill: null };

/** States that mean the feed is healthy again and clear the error slot. */
const HEALTHY = new Set(["connected", "streaming", "backfilled"]);
/** States that describe a fault worth showing in bear colour. */
const FAULTS = new Set(["failed", "error", "unhealthy", "stalled", "backfill_failed"]);

interface Feed {
  bars: Bar[];
  analysis: AnalysisCompleted | null;
  connection: ConnectionState;
  notices: Notices;
  /** Replace the series wholesale - used by a subscribe. */
  reset: (rows: BarRow[], analysis?: AnalysisCompleted | null) => void;
  /** Seed the notice slots from a subscribe reply's `state`. */
  applyStatus: (s: IngestStatus) => void;
}

/**
 * Owns everything arriving over SSE: bars, analyses and ingest status.
 *
 * `onBar` is also handed out so the chart can call series.update() for the
 * newest candle instead of re-setting the whole series on every tick.
 */
export function useFeed(symbol: string, onBar?: (bar: Bar) => void): Feed {
  const [bars, setBars] = useState<Bar[]>([]);
  const [analysis, setAnalysis] = useState<AnalysisCompleted | null>(null);
  const [connection, setConnection] = useState<ConnectionState>("connecting");
  const [notices, setNotices] = useState<Notices>(EMPTY);

  // read inside the SSE callbacks without re-opening the stream on change
  const symbolRef = useRef(symbol);
  const barCb = useRef(onBar);
  useEffect(() => {
    symbolRef.current = symbol;
  }, [symbol]);
  useEffect(() => {
    barCb.current = onBar;
  }, [onBar]);

  const applyStatus = useCallback((s: IngestStatus) => {
    if (s.symbol && s.symbol !== symbolRef.current) return;
    setNotices((n) => {
      const next = { ...n };
      if (s.state === "market_closed") next.market = s;
      if (s.state === "backfilled") next.backfill = s;
      if (FAULTS.has(s.state)) next.problem = s;
      if (HEALTHY.has(s.state)) next.problem = null;
      // a bar arriving is proof the market is open again
      if (s.state === "streaming") next.market = null;
      return next;
    });
  }, []);

  const reset = useCallback((rows: BarRow[], next?: AnalysisCompleted | null) => {
    setBars(rows.map(toBar));
    if (next !== undefined) setAnalysis(next);
    setNotices(EMPTY);
  }, []);

  useEffect(() => {
    const dispose = openFeed({
      onConnection: setConnection,
      onStatus: applyStatus,
      onAnalysis: (a) => {
        if (a.symbol === symbolRef.current) setAnalysis(a);
      },
      onBar: (raw) => {
        if (raw.symbol !== symbolRef.current) return;
        const bar = toBar(raw);
        barCb.current?.(bar);           // incremental series.update()
        setBars((current) => {
          const last = current[current.length - 1];
          if (last && last.time === bar.time) {
            return [...current.slice(0, -1), bar];   // same candle, revised
          }
          if (last && bar.time < last.time) return current;   // out of order
          return [...current, bar];
        });
        // bars are flowing, so nothing is closed or stalled
        setNotices((n) =>
          n.market || n.problem?.kind === "stalled"
            ? { ...n, market: null, problem: n.problem?.kind === "stalled" ? null : n.problem }
            : n,
        );
      },
    });
    return dispose;
  }, [applyStatus]);

  return { bars, analysis, connection, notices, reset, applyStatus };
}
