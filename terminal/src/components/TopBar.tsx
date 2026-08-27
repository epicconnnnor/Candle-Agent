import { Radio, RefreshCw, Settings, Sparkles, Square } from "lucide-react";
import Button from "./ui/Button";
import Select from "./ui/Select";
import SymbolPicker from "./SymbolPicker";
import ConnectionPill from "./ConnectionPill";
import type { ConnectionState, SymbolInfo } from "../api/types";
import type { Phase } from "../hooks/useAnalyze";

interface Props {
  symbol: string;
  interval: string;
  intervals: string[];
  symbols: SymbolInfo[];
  symbolsLoading: boolean;
  symbolsError: string | null;
  connection: ConnectionState;
  onSymbol: (info: SymbolInfo) => void;
  onInterval: (v: string) => void;
  onRefresh: () => void;
  onAnalyze: () => void;
  onSettings: () => void;
  phase: Phase;
  analyzeLabel: string;
  busy: boolean;
  lastPrice: number | null;
  change: number;
  changePct: number;
}

export default function TopBar(p: Props) {
  const up = p.change >= 0;
  const tone = up ? "text-bull" : "text-bear";
  const sign = up ? "+" : "-";

  return (
    <header className="flex flex-wrap items-center gap-2 border-b border-border bg-panel px-4 py-3">
      <SymbolPicker
        symbol={p.symbol}
        symbols={p.symbols}
        loading={p.symbolsLoading}
        error={p.symbolsError}
        onSelect={p.onSymbol}
      />
      <Select
        label="Interval"
        value={p.interval}
        options={p.intervals}
        onChange={p.onInterval}
      />

      <div className="mx-1 h-5 w-px bg-border" />

      <Button onClick={p.onRefresh} disabled={p.busy}>
        <RefreshCw size={16} className={p.busy ? "animate-spin" : ""} />
        {p.busy ? "Loading" : "Refresh"}
      </Button>
      <Button
        variant={p.phase === "idle" ? "primary" : "danger"}
        onClick={p.onAnalyze}
        aria-label={p.phase === "idle" ? "Run analysis" : "Stop analysis"}
      >
        {p.analyzeLabel === "Stop" ? <Square size={16} /> : <Sparkles size={16} />}
        {p.analyzeLabel}
      </Button>

      <div className="mx-1 h-5 w-px bg-border" />

      {/* live is now a property of the stream, not a local timer */}
      <span className="inline-flex h-[34px] shrink-0 items-center gap-2 px-2">
        <Radio size={16} className="text-muted" />
        <span className="lbl">Live</span>
      </span>
      <ConnectionPill state={p.connection} />

      <div className="mx-1 h-5 w-px bg-border" />

      <Button variant="ghost" onClick={p.onSettings} aria-label="Settings" className="px-2">
        <Settings size={16} />
      </Button>

      <div className="ml-auto flex items-baseline gap-3">
        <span className="num text-lg font-medium">
          {p.lastPrice != null ? p.lastPrice.toFixed(2) : "—"}
        </span>
        {p.lastPrice != null && (
          <>
            <span className={`num text-sm ${tone}`}>
              {sign}
              {Math.abs(p.change).toFixed(2)}
            </span>
            <span className={`num text-sm ${tone}`}>
              {sign}
              {Math.abs(p.changePct).toFixed(2)}%
            </span>
          </>
        )}
      </div>
    </header>
  );
}
