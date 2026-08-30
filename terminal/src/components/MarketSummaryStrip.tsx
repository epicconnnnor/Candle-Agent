import Card from "./ui/Card";
import type { Stage1 } from "../api/types";

const DASH = "—";

interface Cell {
  label: string;
  value: string;
  /** Numbers get the monospace face; words get the sans one. */
  numeric?: boolean;
  note?: string;
}

/** Levels split around the last close - the same rule the chart uses. */
function zones(stage1: Stage1 | null, lastClose: number | null) {
  const levels = stage1?.key_levels ?? [];
  if (!levels.length || lastClose === null) return { support: DASH, resistance: DASH };

  const below = levels.filter((l) => l <= lastClose).sort((a, b) => a - b);
  const above = levels.filter((l) => l > lastClose).sort((a, b) => a - b);

  const span = (xs: number[]) =>
    xs.length === 0
      ? DASH
      : xs.length === 1
        ? xs[0].toFixed(2)
        : `${xs[0].toFixed(2)} – ${xs[xs.length - 1].toFixed(2)}`;

  return { support: span(below), resistance: span(above) };
}

export default function MarketSummaryStrip({
  stage1, lastClose,
}: {
  stage1: Stage1 | null;
  lastClose: number | null;
}) {
  const { support, resistance } = zones(stage1, lastClose);

  // what each label asserts, in the two facts it decomposes into
  const CYCLE_NOTE: Record<string, string> = {
    compression: "range steady or narrowing, no direction",
    breakout: "range expanding, price making progress",
    trend: "range steady, price making progress",
    exhaustion: "range expanding, price going nowhere",
  };

  const cells: Cell[] = [
    {
      label: "Current trend",
      value: stage1 ? `${stage1.regime.replace("_", " ")} · ${stage1.strength}` : DASH,
    },
    {
      label: "Current market cycle",
      value: stage1?.cycle ?? DASH,
      note: stage1 ? CYCLE_NOTE[stage1.cycle] : undefined,
    },
    // Stage 1 describes the market; it does not forecast it. That split is
    // deliberate - a diagnosis that predicts cannot be graded against what
    // the window actually did - so there is no "next" to show.
    {
      label: "Next market cycle",
      value: DASH,
      note: "stage 1 describes, it does not forecast",
    },
    { label: "Support zone", value: support, numeric: true },
    { label: "Resistance zone", value: resistance, numeric: true },
  ];

  return (
    <Card title="Market summary">
      <div className="grid grid-cols-2 divide-x divide-border min-[900px]:grid-cols-5">
        {cells.map((c, i) => (
          <div
            key={c.label}
            className={`min-w-0 px-4 ${i === 0 ? "pl-0" : ""} ${
              i === cells.length - 1 ? "pr-0" : ""
            }`}
          >
            <div className="lbl">{c.label}</div>
            <div
              className={`mt-1.5 truncate text-[14px] text-text ${
                c.numeric ? "num" : "font-sans"
              }`}
              title={c.value}
            >
              {c.value}
            </div>
            {c.note && (
              <div className="mt-0.5 font-sans text-[12px] text-muted">{c.note}</div>
            )}
          </div>
        ))}
      </div>
    </Card>
  );
}
