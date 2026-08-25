import Panel from "./ui/Panel";
import type { Stage2 } from "../types";

interface Card {
  label: string;
  price: number;
  reason: string;
  tone: string;
}

export default function LevelCards({ stage2 }: { stage2: Stage2 }) {
  const cards: Card[] = [
    { label: "Entry", price: stage2.entry, reason: stage2.reasons.entry, tone: "text-text" },
    { label: "Stop", price: stage2.stop, reason: stage2.reasons.stop, tone: "text-bear" },
    { label: "Target", price: stage2.target, reason: stage2.reasons.target, tone: "text-bull" },
  ];

  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
      {cards.map((c) => (
        <Panel key={c.label} className="p-4">
          <div className="flex items-baseline justify-between">
            <span className="lbl">{c.label}</span>
            {c.label === "Target" && (
              <span className="lbl">R:R {stage2.risk_reward.toFixed(1)}</span>
            )}
          </div>
          <div className={`num mt-3 text-2xl font-medium ${c.tone}`}>
            {c.price.toFixed(2)}
          </div>
          <p className="mt-3 text-[13px] leading-snug text-muted">{c.reason}</p>
        </Panel>
      ))}
    </div>
  );
}
