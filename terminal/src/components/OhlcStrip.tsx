import type { Bar } from "../types";
import { formatTime } from "../lib/timezone";
import type { Zone } from "../lib/timezone";

interface Props {
  bar: Bar;
  prevClose: number;
  atr: number;
  symbol: string;
  timeframe: string;
  zone: Zone;
}

interface Group {
  label: string;
  value: string;
  tone?: string;
}

/**
 * Label/value contrast rule: 10px muted uppercase label, 13px full-strength
 * monospace value, 6px between the two, 24px between groups with a 1px rule.
 * No icons here - this row is all data.
 */
export default function OhlcStrip({
  bar, prevClose, atr, symbol, timeframe, zone,
}: Props) {
  const up = bar.close >= prevClose;

  const groups: Group[] = [
    // bar.time is epoch SECONDS; every other timestamp here is ms
    { label: "Time", value: formatTime(bar.time * 1000, zone, false) },
    { label: "Open", value: bar.open.toFixed(2) },
    { label: "High", value: bar.high.toFixed(2) },
    { label: "Low", value: bar.low.toFixed(2) },
    { label: "Close", value: bar.close.toFixed(2), tone: up ? "text-bull" : "text-bear" },
    { label: "Volume", value: bar.volume.toLocaleString("en-US") },
    { label: "ATR (14)", value: atr.toFixed(2) },
  ];

  return (
    <div className="flex flex-wrap items-center gap-y-2 border-b border-border px-4 py-2">
      <div className="flex items-baseline pr-3">
        <span className="num text-[13px] font-medium">
          {symbol}
          <span className="text-muted"> / {timeframe}</span>
        </span>
      </div>

      {groups.map((g) => (
        <div
          key={g.label}
          className="flex items-baseline gap-1.5 border-l border-border px-3"
        >
          <span className="lbl">{g.label}</span>
          <span className={`num text-[13px] ${g.tone ?? "text-text"}`}>{g.value}</span>
        </div>
      ))}
    </div>
  );
}
