import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Maximize2 } from "lucide-react";
import TopBar from "./components/TopBar";
import OhlcStrip from "./components/OhlcStrip";
import Chart, { type ChartHandle } from "./components/Chart";
import Stage1Panel from "./components/Stage1Panel";
import DecisionCard from "./components/DecisionCard";
import DecisionPathCard from "./components/DecisionPathCard";
import ReasoningCard from "./components/ReasoningCard";
import SessionCard from "./components/SessionCard";
import LevelsCard from "./components/LevelsCard";
import ChatPanel from "./components/ChatPanel";
import SettingsModal from "./components/SettingsModal";
import StatusBanner from "./components/StatusBanner";
import PipelineStrip, { type Stage } from "./components/PipelineStrip";
import Button from "./components/ui/Button";
import Card from "./components/ui/Card";
import { forgetStoredKey, loadStoredKey, storeKey } from "./apiKeyStorage";
import { useAnalyze } from "./hooks/useAnalyze";
import { useFeed } from "./hooks/useFeed";
import { atr } from "./lib/indicators";
import { loadRecent } from "./lib/recent";
import { freshnessOf } from "./lib/freshness";
import DemoBar from "./components/DemoBar";
import { loadZone, storeZone } from "./lib/timezone";
import type { Zone } from "./lib/timezone";
import {
  getAnalysis, getDemoSample, getDemoSamples, getDemoStatus, getSymbols,
  hasStatus, subscribe, toBar,
} from "./api/client";
import type {
  Bar, DemoSample, DemoSampleSummary, DemoStatus, IngestStatus, Interval,
  SymbolInfo,
} from "./api/types";

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

  // A stored sample REPLACES what is displayed rather than being written
  // into the feed. The live stream keeps running underneath and simply is
  // not shown, so nothing arriving can quietly turn a stored example into
  // something that looks live.
  const [sample, setSample] = useState<DemoSample | null>(null);
  const [samples, setSamples] = useState<DemoSampleSummary[]>([]);
  const [sampleLoading, setSampleLoading] = useState(false);
  // where to go back to: a sample moves the picker so the whole screen
  // agrees with itself, and "Back to live" has to undo exactly that
  const liveBefore = useRef<{ symbol: string; interval: string } | null>(null);
  const [demoStatus, setDemoStatus] = useState<DemoStatus | null>(null);
  const [source, setSource] = useState("—");
  const [settingsOpen, setSettingsOpen] = useState(false);
  // The visitor's LLM key. React state by default, so a reload clears it;
  // localStorage only when the user opts in from Settings. Never a cookie,
  // and never a server record either way.
  //
  // A key already in this browser means the user opted in previously, so
  // both the value and the checkbox come back on. Absent that, persistence
  // is off: a credential is not remembered unless someone asked for it.
  const [apiKey, setApiKeyState] = useState(loadStoredKey);
  const [rememberKey, setRememberKey] = useState(() => Boolean(loadStoredKey()));

  const setApiKey = (key: string) => {
    setApiKeyState(key);
    if (rememberKey) storeKey(key);
  };

  const setRemember = (remember: boolean) => {
    setRememberKey(remember);
    // unchecking drops the saved copy but keeps the key usable for this
    // session - the box controls persistence, not the current request
    if (remember) storeKey(apiKey);
    else forgetStoredKey();
  };

  const forgetKey = () => {
    setApiKeyState("");
    setRememberKey(false);
    forgetStoredKey();
  };

  // A display preference, not a credential, so it persists without being
  // asked for. Nothing stored or sent anywhere changes with it.
  const [zone, setZoneState] = useState<Zone>(loadZone);
  const setZone = (next: Zone) => {
    setZoneState(next);
    storeZone(next);
  };

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
        // Restore the latest analysis for THIS symbol and interval. Scoping
        // the lookup is what keeps the old bug fixed: a verdict from another
        // series can no longer come back and put its levels on this chart.
        const stored = await getAnalysis(res.symbol, nextInterval);
        const matches =
          stored && stored.symbol === res.symbol && stored.interval === nextInterval;
        feed.reset(
          res.bars,
          matches
            ? {
                symbol: stored.symbol,
                bar_ts: stored.ts,
                stage1: stored.stage1,
                stage2: stored.stage2,
                model: stored.model,
                latency_ms: stored.latency_ms,
              }
            : null,
          matches ? stored.price_at : null,
          matches ? stored.atr_at : null,
        );
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

  const refreshDemoStatus = useCallback(() => {
    getDemoStatus().then(setDemoStatus).catch(() => {
      // a missing budget endpoint must not break the terminal
      setDemoStatus(null);
    });
  }, []);

  useEffect(() => {
    getDemoSamples().then((r) => setSamples(r.samples)).catch(() => setSamples([]));
    refreshDemoStatus();
  }, [refreshDemoStatus]);

  // the count only moves when an analysis actually completes
  useEffect(() => {
    if (feed.analysis) refreshDemoStatus();
  }, [feed.analysis, refreshDemoStatus]);

  const loadSample = useCallback(async (id: string) => {
    setSampleLoading(true);
    try {
      const s = await getDemoSample(id);
      // The picker follows the sample. Showing AAPL bars under a TSLA
      // heading is the same class of error as a chart that looks live and
      // is not - every label on screen has to name what is on screen.
      // No load() call: the subscription is deliberately left alone.
      liveBefore.current ??= { symbol, interval };
      setSymbol(s.symbol);
      setInterval(s.interval);
      setSample(s);
      setRevision((r) => r + 1);      // the series is replaced wholesale
    } catch (e) {
      setFault({
        ts: Date.now(), mode: "", state: "error", kind: "error",
        message: e instanceof Error ? e.message : String(e),
      });
    } finally {
      setSampleLoading(false);
    }
  }, []);

  const clearSample = useCallback(() => {
    const back = liveBefore.current;
    liveBefore.current = null;
    setSample(null);
    setRevision((r) => r + 1);
    if (back) {
      setSymbol(back.symbol);
      setInterval(back.interval);
      void load(back.symbol, back.interval);   // resubscribe to the live feed
    }
  }, [load]);

  const onSymbol = useCallback(
    (info: SymbolInfo) => {
      setSample(null);            // an explicit choice leaves the example
      liveBefore.current = null;
      setSymbol(info.symbol);
      void load(info.symbol, interval, info.source);
    },
    [interval, load],
  );

  const onInterval = useCallback(
    (next: string) => {
      setSample(null);
      liveBefore.current = null;
      setInterval(next);
      void load(symbol, next);
    },
    [symbol, load],
  );

  const sampleBars = useMemo(
    () => (sample ? sample.bars.map((b) => toBar({ ...b, symbol: sample.symbol, interval: sample.interval })) : []),
    [sample],
  );
  const bars = sample ? sampleBars : feed.bars;

  // One analysis object for the whole render, from whichever source is on
  // screen. A stored sample carries the market it was formed against, so
  // staleness is judged against its own bars and never against the live
  // price of a symbol it is not showing.
  const analysis = sample
    ? {
        symbol: sample.symbol, interval: sample.interval,
        bar_ts: sample.bar_ts, stage1: sample.stage1, stage2: sample.stage2,
        model: sample.model, latency_ms: sample.latency_ms,
      }
    : feed.analysis;
  const analysisPrice = sample ? sample.price_at : feed.analysisPrice;
  const analysisAtr = sample ? sample.atr_at : feed.analysisAtr;
  const last = bars[bars.length - 1] ?? null;
  const prev = bars[bars.length - 2] ?? last;
  const change = last && prev ? last.close - prev.close : 0;
  const changePct = last && prev && prev.close ? (change / prev.close) * 100 : 0;
  const atr14 = useMemo(() => atr(bars), [bars]);

  const freshness = freshnessOf({
    analysisPrice,
    analysisAtr,
    currentPrice: last?.close ?? null,
    currentAtr: atr14,
  });

  const problem = fault ?? feed.notices.problem;

  /**
   * Pipeline stages, driven only by signals that actually exist:
   * bars.closed, ingest.status, snapshot.built,
   * analysis.stage1.completed and analysis.completed.
   *
   * The chat cell has no backend and still says so.
   */
  const analysing = analyze.phase !== "idle";
  const prog = feed.progress;
  const marketClosed = Boolean(feed.notices.market);

  const dataStage: Stage = problem
    ? { name: "Data", state: "error", status: problem.message ?? "feed error" }
    : bars.length === 0
      ? { name: "Data", state: "waiting", status: "no bars yet" }
      : marketClosed
        ? { name: "Data", state: "done", status: `${bars.length} bars · market closed` }
        : feed.barEvents > 0
          // only an actual bar event proves data is flowing; a connected
          // socket on its own does not
          ? { name: "Data", state: "running", status: `${bars.length} bars · streaming` }
          : { name: "Data", state: "done", status: `${bars.length} bars · history only` };

  const snapshotStage: Stage =
    prog.snapshotAt !== null
      ? { name: "Snapshot", state: "done", status: `${prog.bars ?? "?"} bars packaged` }
      : analysing
        ? { name: "Snapshot", state: "running", status: "building feature packet" }
        : { name: "Snapshot", state: "idle", status: "awaiting next run" };

  // A pipeline cell reports where the run got to, never what it concluded.
  // The verdict is in the cards below, once - repeating it here was the
  // same answer in two places, drifting apart whenever one updated first.
  const stage1Seconds =
    prog.stage1At !== null && prog.snapshotAt !== null
      ? (prog.stage1At - prog.snapshotAt) / 1000
      : null;

  const diagnosisStage: Stage =
    prog.stage1At !== null
      ? { name: "Diagnosis", state: "done",
          status: stage1Seconds !== null ? `done · ${stage1Seconds.toFixed(1)}s` : "done" }
      : prog.snapshotAt !== null
        // snapshot landed, stage 1 has not: it is running right now
        ? { name: "Diagnosis", state: "running", status: "stage 1 running" }
        : analysis
          // restored from storage: it finished, but not in this session, so
          // there is no elapsed time to report
          ? { name: "Diagnosis", state: "done", status: "done" }
          : { name: "Diagnosis", state: "idle", status: "no analysis yet" };

  const decisionStage: Stage =
    prog.completedAt !== null && analysis
      ? { name: "Decision", state: "done", status: "done" }
      : prog.stage1At !== null
        ? { name: "Decision", state: "running", status: "stage 2 running" }
        : analysis
          ? { name: "Decision", state: "done", status: "done" }
          : { name: "Decision", state: "idle", status: "no decision yet" };

  const stages: Stage[] = [
    dataStage,
    snapshotStage,
    diagnosisStage,
    decisionStage,
    analysis
      ? { name: "Follow-up", state: "idle", status: "ask about this analysis" }
      : { name: "Follow-up", state: "idle", status: "needs an analysis" },
  ];

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
          zone={zone}
          bar={last}
          prevClose={prev?.close ?? last.close}
          atr={atr14}
          symbol={symbol}
          timeframe={interval}
        />
      )}

      <DemoBar
        samples={samples}
        active={sample}
        onLoad={loadSample}
        onClear={clearSample}
        loading={sampleLoading}
        status={demoStatus}
        usingOwnKey={Boolean(apiKey)}
        zone={zone}
      />

      <main className="mx-auto flex max-w-[1400px] flex-col gap-4 px-4 py-4">
        {/* alerts are global context like the strip above, not a module */}
        <StatusBanner
          zone={zone}
          problem={problem}
          market={feed.notices.market}
          backfill={feed.notices.backfill}
        />

        <PipelineStrip stages={stages} lastEventAt={feed.lastEventAt} />

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
                  zone={zone}
                  ref={chart}
                  bars={bars}
                  stage1={analysis?.stage1 ?? null}
                  stage2={analysis?.stage2 ?? null}
                  revision={revision}
                  scaleToLevels={freshness.state === "fresh"}
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
              analysis={analysis}
              symbol={symbol}
              interval={interval}
              freshness={freshness}
            />
            <DecisionCard
              stage2={analysis?.stage2 ?? null}
              freshness={freshness}
            />
            <DecisionPathCard stage2={analysis?.stage2 ?? null} />
            <ReasoningCard stage2={analysis?.stage2 ?? null} />
          </div>

          <div className="flex min-w-0 flex-col gap-4">
            <SessionCard
              zone={zone}
              connection={feed.connection}
              usingOwnKey={Boolean(apiKey)}
              source={source}
              lastBarTime={last?.time ?? null}
            />
            <LevelsCard
              stage1={analysis?.stage1 ?? null}
              lastClose={last?.close ?? null}
            />
            <ChatPanel
              symbol={symbol}
              apiKey={apiKey}
              hasAnalysis={Boolean(analysis)}
            />
          </div>
        </div>
      </main>

      {settingsOpen && (
        <SettingsModal
          onClose={() => setSettingsOpen(false)}
          apiKey={apiKey}
          onApiKey={setApiKey}
          zone={zone}
          onZone={setZone}
          remember={rememberKey}
          onRemember={setRemember}
          onForget={forgetKey}
        />
      )}
    </div>
  );
}
