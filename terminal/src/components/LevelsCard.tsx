import Card from "./ui/Card";
import type { Stage1 } from "../api/types";

interface Props {
  stage1: Stage1 | null;
  /** Levels are split around the last close, the same rule the chart uses. */
  lastClose: number | null;
}

export default function LevelsCard({ stage1, lastClose }: Props) {
  const levels = stage1?.key_levels ?? [];

  if (!levels.length) {
    return (
      <Card title="Levels">
        <p className="font-sans text-[13px] text-muted">
          No key levels yet. They come from the stage 1 diagnosis.
        </p>
      </Card>
    );
  }

  const close = lastClose ?? 0;
  const resistance = levels.filter((l) => l > close).sort((a, b) => b - a);
  const support = levels.filter((l) => l <= close).sort((a, b) => b - a);

  const group = (label: string, items: number[], tone: string) =>
    items.length > 0 && (
      <div className="flex flex-col gap-3">
        <span className="font-sans text-[13px] text-muted">{label}</span>
        {items.map((level, i) => (
          <div key={`${label}-${level}`} className="flex items-baseline justify-between gap-4">
            <span className="font-sans text-[13px] text-muted">
              {label[0]}
              {items.length - i}
            </span>
            <span className={`num text-[13px] ${tone}`}>{level.toFixed(2)}</span>
          </div>
        ))}
      </div>
    );

  return (
    <Card title="Levels">
      <div className="flex flex-col gap-3">
        {group("Resistance", resistance, "text-bear")}
        {group("Support", support, "text-bull")}
      </div>
    </Card>
  );
}
