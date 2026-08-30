import { AlertTriangle, FileJson, HelpCircle } from "lucide-react";
import Card, { CardRow, CardRows } from "./ui/Card";
import Button from "./ui/Button";
import type { AnalysisCompleted } from "../api/types";
import type { Freshness } from "../lib/freshness";

/**
 * What each cycle label asserts, in the two facts it decomposes into:
 * whether the range is expanding, and whether price is going anywhere.
 * The word alone is not self-explanatory, so it never appears alone.
 */
const CYCLE_NOTE: Record<string, string> = {
  compression: "range steady or narrowing, no direction",
  breakout: "range expanding, price making progress",
  trend: "range steady, price making progress",
  exhaustion: "range expanding, price going nowhere",
};

interface Props {
  analysis: AnalysisCompleted | null;
  symbol: string;
  interval: string;
  /** Whether price has drifted away from the market this analysis read. */
  freshness: Freshness;
}

/**
 * Nothing is shown while an analysis still describes the market.
 *
 * "Age unknown" is its own state, not a synonym for fresh: rows stored
 * before price_at existed carry no price to compare against, and claiming
 * they are current would be a claim we cannot support.
 */
export function FreshnessMark({ freshness }: { freshness: Freshness }) {
  if (freshness.state === "fresh") return null;

  if (freshness.state === "unknown") {
    return (
      <span
        title="This analysis predates price tracking, so its age cannot be judged."
        className="flex items-center gap-1.5 font-sans text-[13px] text-muted"
      >
        <HelpCircle size={13} />
        Age unknown
      </span>
    );
  }

  return (
    <span
      title={`Price has moved ${freshness.driftAtr.toFixed(1)}x ATR since this analysis was made.`}
      className="flex items-center gap-1.5 font-sans text-[13px] text-bear"
    >
      <AlertTriangle size={13} />
      Stale · {freshness.driftAtr.toFixed(1)}x ATR
    </span>
  );
}

export default function Stage1Panel({
  analysis, symbol, interval, freshness,
}: Props) {
  if (!analysis) {
    return (
      <Card title="Stage 1 diagnosis">
        <p className="font-sans text-[13px] text-muted">
          No analysis yet for {symbol} {interval}. Run Analyze, or wait for the
          next closed bar.
        </p>
      </Card>
    );
  }

  const { stage1, stage2 } = analysis;

  const exportJson = () => {
    const blob = new Blob([JSON.stringify(analysis, null, 2)], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${symbol}-${interval}-analysis.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <Card
      title="Stage 1 diagnosis"
      action={
        <span className="flex items-center gap-3">
          <FreshnessMark freshness={freshness} />
          <Button onClick={exportJson}>
            <FileJson size={16} />
            Export JSON
          </Button>
        </span>
      }
    >
      <p className="max-w-[70ch] font-sans text-[15px] leading-snug text-text">
        {stage1.summary}
      </p>

      {/* previously a bordered 4-cell grid: one boundary per card now */}
      <div className="mt-4">
        <CardRows>
          <CardRow label="Regime">{stage1.regime.replace("_", " ")}</CardRow>
          <CardRow
            label={
              <span className="flex flex-col">
                <span>Cycle</span>
                <span className="text-[12px] leading-snug text-label">
                  {CYCLE_NOTE[stage1.cycle]}
                </span>
              </span>
            }
          >
            {stage1.cycle}
          </CardRow>
          <CardRow label="Strength">{stage1.strength}</CardRow>
          <CardRow label="Confidence">{stage2.confidence}</CardRow>
          <CardRow label="Model">
            {analysis.model} · {analysis.latency_ms} ms
          </CardRow>
        </CardRows>
      </div>
    </Card>
  );
}
