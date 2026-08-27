import Card from "./ui/Card";
import type { Stage2 } from "../api/types";

export default function ReasoningCard({ stage2 }: { stage2: Stage2 | null }) {
  const chain = stage2?.reasoning_chain ?? [];

  return (
    <Card title="Reasoning">
      {chain.length === 0 ? (
        <p className="font-sans text-[13px] text-muted">
          No reasoning yet. It arrives with the next analysis.
        </p>
      ) : (
        <ol className="flex flex-col gap-3">
          {chain.map((step, i) => (
            <li key={i} className="flex gap-3">
              <span className="num shrink-0 text-[13px] text-muted">
                {String(i + 1).padStart(2, "0")}
              </span>
              <span className="font-sans text-[13px] leading-snug text-text">{step}</span>
            </li>
          ))}
        </ol>
      )}
    </Card>
  );
}
