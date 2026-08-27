import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Maximize2 } from "lucide-react";
import TopBar from "./components/TopBar";
import OhlcStrip from "./components/OhlcStrip";
import Chart, { type ChartHandle } from "./components/Chart";
import Stage1Panel from "./components/Stage1Panel";
import LevelCards from "./components/LevelCards";
import ChatPanel from "./components/ChatPanel";
import SettingsModal from "./components/SettingsModal";
import StatusBanner from "./components/StatusBanner";
import Button from "./components/ui/Button";
import { useAnalyze } from "./hooks/useAnalyze";
import { useFeed } from "./hooks/useFeed";
import { atr } from "./lib/indicators";
import { loadRecent } from "./lib/recent";
import { getAnalysis, getSymbols, hasStatus, subscribe } from "./api/client";
import type { Bar, IngestStatus, Interval, SymbolInfo } from "./api/types";

const FALLBACK_SYMBOL = import.meta.env.VITE_DEFAULT_SYMBOL ?? "AAPL";
/** Reopen on the last symbol picked, so a reload keeps your place. */
const INITIAL_SYMBOL = loadRecent()[0] ?? FALLBACK_SYMBOL;
const FALLBACK_INTERVALS: Interval[] = ["1m", "5m", "15m", "1h", "4h", "1d"];

export default function App() {
  const [symbol, setSymbol] = useState(INITIAL_SYMBOL);
  const [interval, setInterval] = useState("1m");
  const [symbols, setSymbols] = useState<SymbolInfo[]>([]);
  const [intervals, setIntervals] = useState<string[]>(FALLBACK_INTERVALS);
  const [symbolsLoading, setSymbolsLoading] = useState(true);
  const [symbolsError, setSymbolsError] = useState<string | null>(null);
  const [subscribing, setSubscribing] = useState(false);
  const [fault, setFault] = useState<IngestStatus | null>(null);
  const [revision, setRevision] = useState(0);
  const [settingsOpen, setSettingsOpen] = useState(false);

  const chart = useRef<ChartHandle>(null);

  // live ticks go straight into the series; React state follows behind
  const pushBar = useCallback((bar: Bar) => chart.current?.update(bar), []);
  const feed = useFeed(symbol, pushBar);

  const analyze = useAnalyze(symbol, (message) =>
    setFault({ ts: Date.now(), mode: "", state: "error", kind: "error", message }),
  );
  const { settle } = analyze;
  useEffect(() => {
    if (feed.analysis) settle();
  }, [feed.analysis, settle]);

  /** Point ingest at a feed, then redraw from the bars it hands back. */
  const load = useCallback(
    async (nextSymbol: string, nextInterval: string, source?: string) => {
      setSubscribing(true);
      setFault(null);
      try {
        const res = await subscribe({
          symbol: nextSymbol,
          interval: nextInterval,
          source,
        });
        const stored = await getAnalysis(res.symbol);
        feed.reset(
          res.bars,
          stored
            ? {
                symbol: stored.symbol,
                bar_ts: stored.ts,
                stage1: stored.stage1,
                stage2: stored.stage2,
                model: stored.model,
                latency_ms: stored.latency_ms,
              }
            : null,
        );
        if (hasStatus(res.state)) feed.applyStatus(res.state);
        setRevision((r) => r + 1);          // tells the chart to setData once
      } catch (e) {
        setFault({
          ts: Date.now(),
          mode: "",
          state: "failed",
          kind: "error",
          message: e instanceof Error ? e.message : String(e),
        });
      } finally {
        setSubscribing(false);
      }
    },
    [feed],
  );

  // symbol catalogue, then an initial subscribe
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await getSymbols();
        if (cancelled) return;
        setSymbols(res.symbols);
        if (res.intervals?.length) setIntervals(res.intervals);
        const start = res.symbols.some((s) => s.symbol === INITIAL_SYMBOL)
          ? INITIAL_SYMBOL
          : (res.symbols[0]?.symbol ?? FALLBACK_SYMBOL);
        setSymbol(start);
        void load(start, interval);
      } catch (e) {
        if (!cancelled) {
          setSymbolsError(e instanceof Error ? e.message : String(e));
          void load(INITIAL_SYMBOL, interval);   // the feed may still work
        }
      } finally {
        if (!cancelled) setSymbolsLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
    // deliberately once: later loads are driven by explicit user choices
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const onSymbol = useCallback(
    (info: SymbolInfo) => {
      setSymbol(info.symbol);
      void load(info.symbol, interval, info.source);
    },
    [interval, load],
  );

  const onInterval = useCallback(
    (next: string) => {
      setInterval(next);
      void load(symbol, next);
    },
    [symbol, load],
  );

  const bars = feed.bars;
  const last = bars[bars.length - 1] ?? null;
  const prev = bars[bars.length - 2] ?? last;
  const change = last && prev ? last.close - prev.close : 0;
  const changePct = last && prev && prev.close ? (change / prev.close) * 100 : 0;
  const atr14 = useMemo(() => atr(bars), [bars]);

  const problem = fault ?? feed.notices.problem;

  return (
    <div className="min-h-screen bg-base">
      <TopBar
        symbol={symbol}
        interval={interval}
        intervals={intervals}
        symbols={symbols}
        symbolsLoading={symbolsLoading}
        symbolsError={symbolsError}
        connection={feed.connection}
        onSymbol={onSymbol}
        onInterval={onInterval}
        onRefresh={() => void load(symbol, interval)}
        onAnalyze={() => void analyze.toggle()}
        onSettings={() => setSettingsOpen(true)}
        phase={analyze.phase}
        analyzeLabel={analyze.label}
        busy={subscribing}
        lastPrice={last?.close ?? null}
        change={change}
        changePct={changePct}
      />

      {last && (
        <OhlcStrip
          bar={last}
          prevClose={prev?.close ?? last.close}
          atr={atr14}
          symbol={symbol}
          timeframe={interval}
        />
      )}

      <main className="mx-auto flex max-w-[1400px] flex-col gap-4 px-4 py-4">
        <StatusBanner
          problem={problem}
          market={feed.notices.market}
          backfill={feed.notices.backfill}
        />

        {/* a subscribe dims the old chart rather than blanking the screen */}
        <div
          className={`relative h-[420px] overflow-hidden rounded-lg border border-border
                      transition-opacity duration-200
                      ${subscribing ? "pointer-events-none opacity-40" : "opacity-100"}`}
        >
          <Chart
            ref={chart}
            bars={bars}
            stage1={feed.analysis?.stage1 ?? null}
            stage2={feed.analysis?.stage2 ?? null}
            revision={revision}
          />
          <Button
            onClick={() => chart.current?.resetZoom()}
            className="absolute top-3 left-3 z-10"
          >
            <Maximize2 size={16} />
            Reset zoom
          </Button>
          {bars.length === 0 && !subscribing && (
            <p className="absolute inset-0 flex items-center justify-center text-[13px] text-muted">
              No bars for {symbol} {interval}.
            </p>
          )}
        </div>

        <Stage1Panel analysis={feed.analysis} symbol={symbol} interval={interval} />
        <LevelCards stage2={feed.analysis?.stage2 ?? null} />

        <ChatPanel />
      </main>

      {settingsOpen && <SettingsModal onClose={() => setSettingsOpen(false)} />}
    </div>
  );
}
