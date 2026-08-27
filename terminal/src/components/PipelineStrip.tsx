import { useEffect, useState } from "react";
import Card from "./ui/Card";

export type StageState = "idle" | "waiting" | "running" | "done" | "error";

export interface Stage {
  name: string;
  state: StageState;
  /** One line under the name saying what is actually happening. */
  status: string;
}

const DOT: Record<StageState, string> = {
  // hollow ring
  idle: "border border-border",
  waiting: "border border-muted",
  // filled
  running: "bg-bull animate-pulse",
  done: "bg-bull",
  error: "bg-bear",
};

function Dot({ state }: { state: StageState }) {
  return (
    <span
      aria-hidden="true"
      className={`mt-[5px] h-2 w-2 shrink-0 rounded-full ${DOT[state]}`}
    />
  );
}

/** 2px track under each cell; fills teal only while that stage runs. */
function Track({ state }: { state: StageState }) {
  return (
    <div className="mt-3 h-[2px] w-full bg-border">
      {state === "running" && <div className="track-sweep h-full bg-bull" />}
      {state === "done" && <div className="h-full w-full bg-bull" />}
      {state === "error" && <div className="h-full w-full bg-bear" />}
    </div>
  );
}

function Ago({ since }: { since: number | null }) {
  const [, tick] = useState(0);

  // re-render once a second so the age stays honest without polling anything
  useEffect(() => {
    const id = setInterval(() => tick((n) => n + 1), 1000);
    return () => clearInterval(id);
  }, []);

  const text =
    since === null ? "—" : `${Math.max(0, Math.round((Date.now() - since) / 1000))}s ago`;

  return (
    <span className="num text-[11px] text-muted">Last refresh: {text}</span>
  );
}

export default function PipelineStrip({
  stages, lastEventAt,
}: {
  stages: Stage[];
  lastEventAt: number | null;
}) {
  return (
    <Card title="Pipeline" action={<Ago since={lastEventAt} />}>
      <div className="grid grid-cols-2 divide-x divide-border min-[900px]:grid-cols-5">
        {stages.map((s, i) => (
          <div
            key={s.name}
            className={`min-w-0 px-4 ${i === 0 ? "pl-0" : ""} ${
              i === stages.length - 1 ? "pr-0" : ""
            }`}
          >
            <div className="flex items-start gap-2">
              <Dot state={s.state} />
              <div className="min-w-0">
                <div className="font-sans text-[13px] font-medium text-text">{s.name}</div>
                <div className="mt-0.5 font-sans text-[12px] leading-snug text-muted">
                  {s.status}
                </div>
              </div>
            </div>
            <Track state={s.state} />
          </div>
        ))}
      </div>
    </Card>
  );
}
