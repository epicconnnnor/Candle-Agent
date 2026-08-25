import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import TopBar from "./components/TopBar";
import OhlcStrip from "./components/OhlcStrip";
import Chart, { type ChartHandle } from "./components/Chart";
import Stage1Panel from "./components/Stage1Panel";
import LevelCards from "./components/LevelCards";
import ChatPanel from "./components/ChatPanel";
import SettingsModal from "./components/SettingsModal";
import { Maximize2 } from "lucide-react";
import Button from "./components/ui/Button";
import { useAnalyze } from "./hooks/useAnalyze";
import { analysis as seedAnalysis, atr, initialBars, nextBar } from "./mock/data";
import type { Analysis, Bar } from "./types";

const LIVE_MS = 1800;

export default function App() {
  const [bars, setBars] = useState<Bar[]>(initialBars);
  const [analysis, setAnalysis] = useState<Analysis>(seedAnalysis);
  const [symbol, setSymbol] = useState(seedAnalysis.symbol);
  const [timeframe, setTimeframe] = useState(seedAnalysis.timeframe);
  const [live, setLive] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);

  const chart = useRef<ChartHandle>(null);

  const last = bars[bars.length - 1];
  const prev = bars[bars.length - 2] ?? last;
  const change = last.close - prev.close;
  const changePct = (change / prev.close) * 100;
  const atr14 = useMemo(() => atr(bars), [bars]);

  /** Append one bar and push it straight into the series (no full reset). */
  const pushBar = useCallback(() => {
    setBars((current) => {
      const bar = nextBar(current[current.length - 1]);
      chart.current?.update(bar);
      return [...current, bar];
    });
  }, []);

  // live stream
  useEffect(() => {
    if (!live) return;
    const id = setInterval(pushBar, LIVE_MS);
    return () => clearInterval(id);
  }, [live, pushBar]);

  const onAnalyzeComplete = useCallback(() => {
    setAnalysis((a) => ({ ...a, ts: Date.now(), latency_ms: 1600 + Math.round(Math.random() * 900) }));
  }, []);

  const { phase, label, toggle } = useAnalyze(onAnalyzeComplete);

  const onFetch = useCallback(() => {
    setLive(false);
    setBars(initialBars);
  }, []);

  return (
    <div className="min-h-screen bg-base">
      <TopBar
        symbol={symbol}
        timeframe={timeframe}
        onSymbol={setSymbol}
        onTimeframe={setTimeframe}
        onFetch={onFetch}
        onAnalyze={toggle}
        onIncremental={pushBar}
        onToggleLive={() => setLive((v) => !v)}
        onSettings={() => setSettingsOpen(true)}
        phase={phase}
        analyzeLabel={label}
        live={live}
        lastPrice={last.close}
        change={change}
        changePct={changePct}
      />

      <OhlcStrip
        bar={last}
        prevClose={prev.close}
        atr={atr14}
        symbol={symbol}
        timeframe={timeframe}
      />

      <main className="mx-auto flex max-w-[1400px] flex-col gap-4 px-4 py-4">
        <div className="relative h-[420px] overflow-hidden rounded-lg border border-border">
          <Chart
            ref={chart}
            bars={bars}
            stage1={analysis.stage1}
            stage2={analysis.stage2}
          />
          <Button
            onClick={() => chart.current?.resetZoom()}
            className="absolute top-3 left-3 z-10"
          >
            <Maximize2 size={16} />
            Reset zoom
          </Button>
        </div>

        <Stage1Panel analysis={analysis} />
        <LevelCards stage2={analysis.stage2} />

        <ChatPanel />
      </main>

      {settingsOpen && <SettingsModal onClose={() => setSettingsOpen(false)} />}
    </div>
  );
}
