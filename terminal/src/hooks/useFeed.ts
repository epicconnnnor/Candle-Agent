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
  /** Market at the moment the analysis was produced, for staleness. */
  analysisPrice: number | null;
  analysisAtr: number | null;
  connection: ConnectionState;
  notices: Notices;
  /** Replace the series wholesale - used by a subscribe. */
  reset: (
    rows: BarRow[],
    analysis?: AnalysisCompleted | null,
    priceAt?: number | null,
    atrAt?: number | null,
  ) => void;
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
  const [analysisPrice, setAnalysisPrice] = useState<number | null>(null);
  const [analysisAtr, setAnalysisAtr] = useState<number | null>(null);
  const [connection, setConnection] = useState<ConnectionState>("connecting");
  const [notices, setNotices] = useState<Notices>(EMPTY);

  // read inside the SSE callbacks without re-opening the stream on change
  const symbolRef = useRef(symbol);
  const lastCloseRef = useRef<number | null>(null);
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

  const reset = useCallback((
    rows: BarRow[],
    next?: AnalysisCompleted | null,
    priceAt?: number | null,
    atrAt?: number | null,
  ) => {
    const mapped = rows.map(toBar);
    setBars(mapped);
    lastCloseRef.current = mapped.length ? mapped[mapped.length - 1].close : null;
    // A restored analysis brings the market it was formed against with it.
    // Passing null clears - which is what pulls old price lines off the chart.
    setAnalysis(next ?? null);
    setAnalysisPrice(next ? (priceAt ?? null) : null);
    setAnalysisAtr(next ? (atrAt ?? null) : null);
    setNotices(EMPTY);
  }, []);

  useEffect(() => {
    const dispose = openFeed({
      onConnection: setConnection,
      onStatus: applyStatus,
      onAnalysis: (a) => {
        if (a.symbol !== symbolRef.current) return;
        setAnalysis(a);
        // a live analysis is formed against the price we have right now;
        // the ATR is recomputed by the caller from the same bars
        setAnalysisPrice(lastCloseRef.current);
        setAnalysisAtr(null);
      },
      onBar: (raw) => {
        if (raw.symbol !== symbolRef.current) return;
        const bar = toBar(raw);
        lastCloseRef.current = bar.close;
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

  return {
    bars, analysis, analysisPrice, analysisAtr, connection, notices, reset, applyStatus,
  };
}
