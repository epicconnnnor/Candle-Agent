import { AlertTriangle, FileJson } from "lucide-react";
import Card, { CardRow, CardRows } from "./ui/Card";
import Button from "./ui/Button";
import type { AnalysisCompleted } from "../api/types";

interface Props {
  analysis: AnalysisCompleted | null;
  symbol: string;
  interval: string;
  /** Price has drifted more than 2x ATR since this analysis was made. */
  stale: boolean;
  driftAtr: number;
}

/** Shown when price has walked away from the market this analysis read. */
export function StaleMark({ driftAtr }: { driftAtr: number }) {
  return (
    <span
      title={`Price has moved ${driftAtr.toFixed(1)}x ATR since this analysis was made.`}
      className="flex items-center gap-1.5 font-sans text-[13px] text-bear"
    >
      <AlertTriangle size={13} />
      Stale · {driftAtr.toFixed(1)}x ATR
    </span>
  );
}

export default function Stage1Panel({
  analysis, symbol, interval, stale, driftAtr,
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
          {stale && <StaleMark driftAtr={driftAtr} />}
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
