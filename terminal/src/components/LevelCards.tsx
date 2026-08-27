import Panel from "./ui/Panel";
import type { Stage2 } from "../api/types";

interface Card {
  label: string;
  price: number | null;
  tone: string;
}

const DECISION_LABEL: Record<string, string> = {
  buy_limit: "Buy limit",
  sell_limit: "Sell limit",
  buy_stop: "Buy stop",
  sell_stop: "Sell stop",
  market_buy: "Market buy",
  market_sell: "Market sell",
  no_trade: "No trade",
};

export default function LevelCards({ stage2 }: { stage2: Stage2 | null }) {
  if (!stage2 || stage2.decision === "no_trade") {
    return (
      <Panel className="p-5">
        <span className="lbl">Stage 2 decision</span>
        <p className="mt-3 text-[13px] text-muted">
          {stage2
            ? "No trade. Stage 2 found no setup that follows from the diagnosis — a valid and frequent answer."
            : "No decision yet."}
        </p>
      </Panel>
    );
  }

  const cards: Card[] = [
    { label: "Entry", price: stage2.entry, tone: "text-text" },
    { label: "Stop", price: stage2.stop, tone: "text-bear" },
    { label: "Target", price: stage2.target, tone: "text-bull" },
  ];

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-3 px-1">
        <span className="lbl">Stage 2 decision</span>
        <span className="lbl text-ctl-text">
          {DECISION_LABEL[stage2.decision] ?? stage2.decision}
        </span>
        <span className="lbl">confidence {stage2.confidence}</span>
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        {cards.map((c) => (
          <Panel key={c.label} className="p-4">
            <div className="flex items-baseline justify-between">
              <span className="lbl">{c.label}</span>
              {c.label === "Target" && stage2.risk_reward != null && (
                <span className="lbl">R:R {stage2.risk_reward.toFixed(1)}</span>
              )}
            </div>
            <div className={`num mt-3 text-2xl font-medium ${c.tone}`}>
              {c.price != null ? c.price.toFixed(2) : "—"}
            </div>
          </Panel>
        ))}
      </div>

      {stage2.reasoning_chain.length > 0 && (
        <Panel className="px-5 py-4">
          <span className="lbl">Reasoning</span>
          <ol className="mt-2">
            {stage2.reasoning_chain.map((step, i) => (
              <li key={i} className="flex gap-3 py-1">
                <span className="lbl mt-1 shrink-0">{String(i + 1).padStart(2, "0")}</span>
                <span className="text-[13px] leading-snug text-muted">{step}</span>
              </li>
            ))}
          </ol>
        </Panel>
      )}
    </div>
  );
}
