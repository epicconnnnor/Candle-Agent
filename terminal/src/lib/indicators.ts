import type { Bar } from "../api/types";

/** Wilder ATR over the closing series. */
export function atr(bars: Bar[], period = 14): number {
  if (bars.length < 2) return 0;
  const trs = bars.slice(1).map((b, i) => {
    const p = bars[i];
    return Math.max(b.high - b.low, Math.abs(b.high - p.close), Math.abs(b.low - p.close));
  });
  const window = trs.slice(-period);
  return window.reduce((a, b) => a + b, 0) / window.length;
}
