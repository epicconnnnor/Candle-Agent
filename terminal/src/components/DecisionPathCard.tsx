import { ShieldCheck } from "lucide-react";
import Card from "./ui/Card";
import { PATH_NODES } from "../api/types";
import type { PathNode, PathStep, Stage2 } from "../api/types";

/**
 * Which nodes the validator holds against the geometry, and which it can
 * only take the model's word for.
 *
 * stop_placement is the honest exception. The playbooks ask for a stop
 * "beyond a real swing point", and detecting swings is not something this
 * codebase does - so only the unambiguous half is enforced: a stop closer
 * than 0.5x ATR is inside the noise it is meant to sit outside of. The
 * rest of that answer is the model's assertion.
 */
const CHECKED: Record<PathNode, string | null> = {
  trend_alignment: "checked against the diagnosed regime and the trade direction",
  level_proximity: "checked against the diagnosed key levels, within 0.5x ATR",
  stop_placement: null,
  risk_reward: "recomputed from entry, stop and target against the 1.5 floor",
};

const LABEL: Record<PathNode, string> = {
  trend_alignment: "Trend alignment",
  level_proximity: "Level proximity",
  stop_placement: "Stop placement",
  risk_reward: "Risk / reward",
};

export default function DecisionPathCard({ stage2 }: { stage2: Stage2 | null }) {
  const path = stage2?.decision_path ?? [];
  // render in the checklist's own order, not whatever order it arrived in
  const byNode = new Map<string, PathStep>(path.map((s) => [s.node, s]));

  return (
    <Card title="Decision path">
      {path.length === 0 ? (
        <p className="font-sans text-[13px] text-muted">
          No checklist yet. It arrives with the next analysis.
        </p>
      ) : (
        <>
          <ol className="flex flex-col gap-3">
            {PATH_NODES.map((node, i) => {
              const step = byNode.get(node);
              const answered = step?.answer ?? "—";
              // "na" means the question did not apply on this row, so nothing
              // was verified even where the node is checkable
              const verified = Boolean(CHECKED[node]) && answered !== "na";
              return (
                <li key={node} className="flex gap-3">
                  <span className="num shrink-0 text-[13px] text-muted">
                    {String(i + 1).padStart(2, "0")}
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                      <span className="font-sans text-[13px] text-muted">
                        {LABEL[node]}
                      </span>
                      <span
                        className={`num text-[13px] ${
                          answered === "na" ? "text-muted" : "text-text"
                        }`}
                      >
                        {answered.replace(/_/g, " ")}
                      </span>
                      {verified && (
                        <span
                          className="inline-flex items-center gap-1 text-bull"
                          title={CHECKED[node] ?? undefined}
                        >
                          <ShieldCheck size={12} aria-hidden="true" />
                          <span className="font-sans text-[11px]">verified</span>
                        </span>
                      )}
                    </div>
                    {step?.because && (
                      <p className="mt-0.5 font-sans text-[13px] leading-snug text-text">
                        {step.because}
                      </p>
                    )}
                  </div>
                </li>
              );
            })}
          </ol>
          <p className="mt-4 font-sans text-[11px] leading-snug text-muted">
            <ShieldCheck size={11} className="mr-1 inline align-[-1px]" aria-hidden="true" />
            Verified answers were recomputed from the numbers in this decision and
            rejected if they disagreed. Stop placement is the model&apos;s own
            assertion — only a stop inside the noise is enforced.
          </p>
        </>
      )}
    </Card>
  );
}
