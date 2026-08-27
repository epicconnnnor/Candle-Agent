import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Maximize2 } from "lucide-react";
import TopBar from "./components/TopBar";
import OhlcStrip from "./components/OhlcStrip";
import Chart, { type ChartHandle } from "./components/Chart";
import Stage1Panel from "./components/Stage1Panel";
import DecisionCard from "./components/DecisionCard";
import ReasoningCard from "./components/ReasoningCard";
import SessionCard from "./components/SessionCard";
import LevelsCard from "./components/LevelsCard";
import ChatPanel from "./components/ChatPanel";
import SettingsModal from "./components/SettingsModal";
import StatusBanner from "./components/StatusBanner";
import Button from "./components/ui/Button";
import Card from "./components/ui/Card";
import { useAnalyze } from "./hooks/useAnalyze";
import { useFeed } from "./hooks/useFeed";
import { atr } from "./lib/indicators";
import { loadRecent } from "./lib/recent";
import { getSymbols, hasStatus, subscribe } from "./api/client";
import type { Bar, IngestStatus, Interval, SymbolInfo } from "./api/types";

const FALLBACK_SYMBOL = import.meta.env.VITE_DEFAULT_SYMBOL ?? "AAPL";
/** Reopen on the last symbol picked, so a reload keeps your place. */
const INITIAL_SYMBOL = loadRecent()[0] ?? FALLBACK_SYMBOL;
const FALLBACK_INTERVALS: Interval[] = ["1m", "5m", "15m", "1h", "4h", "1d"];
/** Price drift, in ATRs, past which an analysis is marked stale. */
const STALE_ATR_MULTIPLE = 2;

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
  const [source, setSource] = useState("—");
  const [settingsOpen, setSettingsOpen] = useState(false);
  // The visitor's LLM key. React state ONLY: never localStorage,
  // sessionStorage or a cookie, so a reload clears it. Deliberate - there
  // is no "remember my key".
  const [apiKey, setApiKey] = useState("");

  const chart = useRef<ChartHandle>(null);

  // live ticks go straight into the series; React state follows behind
  const pushBar = useCallback((bar: Bar) => chart.current?.update(bar), []);
  const feed = useFeed(symbol, pushBar);

  const analyze = useAnalyze(symbol, apiKey, (message) =>
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
        // Deliberately NOT restoring the stored analysis. It was formed
        // against a different symbol, interval or price, and rehydrating it
        // put stale entry/stop/target lines on the chart - which then
        // stretched the y-axis to reach them. A fresh one arrives on the
        // next closed bar, or on demand via Analyze.
        feed.reset(res.bars, null);
        setSource(res.source);
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

  // An analysis describes the market at one price. Once price has walked
  // more than 2x ATR away from that, the levels it named are no longer a
  // description of what is on screen - say so rather than showing them plain.
  const drift =
    last && feed.analysisPrice != null ? Math.abs(last.close - feed.analysisPrice) : 0;
  const driftAtr = atr14 > 0 ? drift / atr14 : 0;
  const stale = Boolean(feed.analysis) && driftAtr > STALE_ATR_MULTIPLE;

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
        usingOwnKey={Boolean(apiKey)}
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
        {/* alerts are global context like the strip above, not a module */}
        <StatusBanner
          problem={problem}
          market={feed.notices.market}
          backfill={feed.notices.backfill}
        />

        {/* main column and sidebar at 2:1; the sidebar stacks below 1100px */}
        <div className="grid grid-cols-1 gap-4 min-[1100px]:grid-cols-[2fr_1fr]">
          <div className="flex min-w-0 flex-col gap-4">
            <Card
              title={`${symbol} · ${interval}`}
              action={
                <Button onClick={() => chart.current?.resetZoom()}>
                  <Maximize2 size={16} />
                  Reset zoom
                </Button>
              }
            >
              {/* a subscribe dims the old chart rather than blanking it */}
              <div
                className={`relative h-[420px] overflow-hidden transition-opacity duration-200
                            ${subscribing ? "pointer-events-none opacity-40" : "opacity-100"}`}
              >
                <Chart
                  ref={chart}
                  bars={bars}
                  stage1={feed.analysis?.stage1 ?? null}
                  stage2={feed.analysis?.stage2 ?? null}
                  revision={revision}
                />
                {bars.length === 0 && !subscribing && (
                  <p className="absolute inset-0 flex items-center justify-center
                                font-sans text-[13px] text-muted">
                    No bars for {symbol} {interval}.
                  </p>
                )}
              </div>
            </Card>

            <Stage1Panel
              analysis={feed.analysis}
              symbol={symbol}
              interval={interval}
              stale={stale}
              driftAtr={driftAtr}
            />
            <DecisionCard stage2={feed.analysis?.stage2 ?? null} stale={stale} />
            <ReasoningCard stage2={feed.analysis?.stage2 ?? null} />
          </div>

          <div className="flex min-w-0 flex-col gap-4">
            <SessionCard
              connection={feed.connection}
              usingOwnKey={Boolean(apiKey)}
              source={source}
              lastBarTime={last?.time ?? null}
            />
            <LevelsCard
              stage1={feed.analysis?.stage1 ?? null}
              lastClose={last?.close ?? null}
            />
            <ChatPanel />
          </div>
        </div>
      </main>

      {settingsOpen && (
        <SettingsModal
          onClose={() => setSettingsOpen(false)}
          apiKey={apiKey}
          onApiKey={setApiKey}
        />
      )}
    </div>
  );
}
