import { useEffect, useState } from "react";
import { FileJson, GitBranch } from "lucide-react";
import Button from "./ui/Button";
import type { Analysis } from "../types";

interface Props {
  analysis: Analysis;
}

const REPLAY_MS = 700;

export default function Stage1Panel({ analysis }: Props) {
  const { stage1, stage2 } = analysis;
  const bull = stage1.bias === "bull";
  const [replayStep, setReplayStep] = useState(-1); // -1 = not replaying

  // reveal the reasoning chain one step at a time
  useEffect(() => {
    if (replayStep < 0) return;
    if (replayStep >= stage2.reasoning_chain.length) return;
    const t = setTimeout(() => setReplayStep((s) => s + 1), REPLAY_MS);
    return () => clearTimeout(t);
  }, [replayStep, stage2.reasoning_chain.length]);

  const exportJson = () => {
    const blob = new Blob([JSON.stringify(analysis, null, 2)], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${analysis.symbol}-${analysis.timeframe}-analysis.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const stats = [
    { label: "Cycle", value: stage1.cycle },
    { label: "H / L count", value: `${stage1.hl_count.highs}H / ${stage1.hl_count.lows}L` },
    { label: "EMA state", value: stage1.ema_state },
    { label: "Confidence", value: stage1.confidence },
  ];

  return (
    <section
      className={`rounded-lg border border-border bg-panel border-l-2 ${
        bull ? "border-l-bull" : "border-l-bear"
      }`}
    >
      <div className="flex items-start justify-between gap-4 p-5">
        <div className="min-w-0">
          <div className="flex items-center gap-3">
            <span className="lbl">Stage 1 diagnosis</span>
            <span className={`lbl ${bull ? "text-bull" : "text-bear"}`}>
              {stage1.regime.replace("_", " ")} / {stage1.strength}
            </span>
          </div>
          <h2 className="mt-3 max-w-[68ch] text-[17px] leading-snug font-medium">
            {stage1.diagnosis}
          </h2>
        </div>
        <div className="flex shrink-0 gap-2">
          <Button
            onClick={() => setReplayStep(replayStep < 0 ? 0 : -1)}
            variant={replayStep >= 0 ? "active" : "default"}
          >
            <GitBranch size={16} />
            Replay decision tree
          </Button>
          <Button onClick={exportJson}>
            <FileJson size={16} />
            Export JSON
          </Button>
        </div>
      </div>

      <dl className="grid grid-cols-2 border-t border-border sm:grid-cols-4">
        {stats.map((s, i) => (
          <div
            key={s.label}
            className={`px-5 py-4 ${i > 0 ? "sm:border-l sm:border-border" : ""} ${
              i % 2 === 1 ? "border-l border-border" : ""
            } ${i < 2 ? "border-b border-border sm:border-b-0" : ""}`}
          >
            <dt className="lbl">{s.label}</dt>
            <dd className="num mt-1.5 text-[13px] text-text uppercase">{s.value}</dd>
          </div>
        ))}
      </dl>

      {replayStep >= 0 && (
        <ol className="border-t border-border px-5 py-4">
          {stage2.reasoning_chain.map((step, i) => (
            <li
              key={i}
              className={`flex gap-3 py-1.5 transition-opacity duration-300 ${
                i <= replayStep ? "opacity-100" : "opacity-20"
              }`}
            >
              <span className="lbl mt-1 shrink-0">{String(i + 1).padStart(2, "0")}</span>
              <span className="text-[13px] leading-snug text-muted">{step}</span>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
