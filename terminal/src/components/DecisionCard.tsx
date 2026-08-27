import Card from "./ui/Card";
import type { Stage2 } from "../api/types";
import type { Freshness } from "../lib/freshness";

const DECISION_LABEL: Record<string, string> = {
  buy_limit: "Buy limit",
  sell_limit: "Sell limit",
  buy_stop: "Buy stop",
  sell_stop: "Sell stop",
  market_buy: "Market buy",
  market_sell: "Market sell",
  no_trade: "No trade",
};

/**
 * Entry, stop and target in ONE card.
 *
 * They were three separate panels, which read as three unrelated numbers.
 * They are one decision, so they share a boundary and sit on a single row
 * divided only by whitespace - no nested borders.
 */
export default function DecisionCard({
  stage2, freshness,
}: { stage2: Stage2 | null; freshness: Freshness }) {
  const title = "Decision";
  const stale = freshness.state === "stale";

  if (!stage2) {
    return (
      <Card title={title}>
        <p className="font-sans text-[13px] text-muted">No decision yet.</p>
      </Card>
    );
  }

  const label = DECISION_LABEL[stage2.decision] ?? stage2.decision;

  if (stage2.decision === "no_trade") {
    return (
      <Card
        title={title}
        action={<span className="font-sans text-[13px] text-muted">{label}</span>}
      >
        <p className="font-sans text-[13px] leading-snug text-muted">
          Stage 2 found no setup that follows from the diagnosis — a valid and
          frequent answer.
        </p>
      </Card>
    );
  }

  const cells: { label: string; value: number | null; tone: string }[] = [
    { label: "Entry", value: stage2.entry, tone: "text-text" },
    { label: "Stop", value: stage2.stop, tone: "text-bear" },
    { label: "Target", value: stage2.target, tone: "text-bull" },
  ];

  return (
    <Card
      title={title}
      action={
        <span className="flex items-baseline gap-3">
          <span className="font-sans text-[13px] text-text">{label}</span>
          <span className="font-sans text-[13px] text-muted">
            confidence {stage2.confidence}
          </span>
          {stage2.risk_reward != null && (
            <span className="num text-[13px] text-muted">
              R:R {stage2.risk_reward.toFixed(1)}
            </span>
          )}
          {stale && <span className="font-sans text-[13px] text-bear">Stale</span>}
          {freshness.state === "unknown" && (
            <span className="font-sans text-[13px] text-muted">Age unknown</span>
          )}
        </span>
      }
    >
      {/* stale levels stay visible but recede: they are history, not advice */}
      <div className={`grid grid-cols-3 gap-4 ${stale ? "opacity-50" : ""}`}>
        {cells.map((c) => (
          <div key={c.label} className="flex flex-col gap-1.5">
            <span className="font-sans text-[13px] text-muted">{c.label}</span>
            <span className={`num text-2xl font-medium ${c.tone}`}>
              {c.value != null ? c.value.toFixed(2) : "—"}
            </span>
          </div>
        ))}
      </div>
    </Card>
  );
}
