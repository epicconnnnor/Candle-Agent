import { Download, Radio, RefreshCw, Settings, Sparkles, Square } from "lucide-react";
import Button from "./ui/Button";
import Select from "./ui/Select";
import { SYMBOLS, TIMEFRAMES } from "../mock/data";
import type { Phase } from "../hooks/useAnalyze";

interface Props {
  symbol: string;
  timeframe: string;
  onSymbol: (v: string) => void;
  onTimeframe: (v: string) => void;
  onFetch: () => void;
  onAnalyze: () => void;
  onIncremental: () => void;
  onToggleLive: () => void;
  onSettings: () => void;
  phase: Phase;
  analyzeLabel: string;
  live: boolean;
  lastPrice: number;
  change: number;
  changePct: number;
}

export default function TopBar(p: Props) {
  const up = p.change >= 0;
  const tone = up ? "text-bull" : "text-bear";
  const sign = up ? "+" : "-";

  return (
    <header className="flex flex-wrap items-center gap-2 border-b border-border bg-panel px-4 py-3">
      <Select label="Symbol" value={p.symbol} options={SYMBOLS} onChange={p.onSymbol} />
      <Select label="Timeframe" value={p.timeframe} options={TIMEFRAMES} onChange={p.onTimeframe} />

      <div className="mx-1 h-5 w-px bg-border" />

      <Button onClick={p.onFetch}>
        <Download size={16} />
        Fetch data
      </Button>
      <Button
        variant="primary"
        onClick={p.onAnalyze}
        aria-label={p.phase === "idle" ? "Run analysis" : "Stop analysis"}
        className={p.phase !== "idle" ? "group" : ""}
      >
        {p.phase === "idle" ? (
          <>
            <Sparkles size={16} />
            {p.analyzeLabel}
          </>
        ) : (
          <>
            <span className="flex items-center gap-2 group-hover:hidden">
              <Sparkles size={16} />
              {p.analyzeLabel}
            </span>
            <span className="hidden items-center gap-2 group-hover:flex">
              <Square size={16} />
              Stop
            </span>
          </>
        )}
      </Button>
      <Button onClick={p.onIncremental}>
        <RefreshCw size={16} />
        Incremental
      </Button>
      <Button variant={p.live ? "active" : "default"} onClick={p.onToggleLive}>
        <Radio size={16} />
        Live {p.live ? "on" : "off"}
      </Button>
      <Button variant="ghost" onClick={p.onSettings} aria-label="Settings" className="px-2">
        <Settings size={16} />
      </Button>

      <div className="ml-auto flex items-baseline gap-3">
        <span className="num text-lg font-medium">{p.lastPrice.toFixed(2)}</span>
        <span className={`num text-sm ${tone}`}>
          {sign}
          {Math.abs(p.change).toFixed(2)}
        </span>
        <span className={`num text-sm ${tone}`}>
          {sign}
          {Math.abs(p.changePct).toFixed(2)}%
        </span>
      </div>
    </header>
  );
}
