import type { Analysis } from "../data";
import { fmtPrice, humanize } from "../data";

/** The four numbers the decision actually turns on. */
function keyLevels(a: Analysis) {
  return [
    { label: "Entry", value: fmtPrice(a.stage2.entry) },
    { label: "Stop", value: fmtPrice(a.stage2.stop) },
    { label: "Target", value: fmtPrice(a.stage2.target) },
    { label: "Risk / Reward", value: `1:${a.stage2.risk_reward.toFixed(1)}` },
  ];
}

export default function DiagnosisBlock({ analysis }: { analysis: Analysis }) {
  const { stage1, stage2 } = analysis;

  return (
    <section className="bg-navy text-paper">
      <div className="px-8 pt-10 pb-9 sm:px-12">
        <div className="flex flex-wrap items-baseline justify-between gap-x-8 gap-y-3">
          <span className="label text-paper/55">Current diagnosis</span>
          <span className="label text-paper/55">
            Stage 1 &middot; {analysis.model}
          </span>
        </div>

        <h2 className="mt-7 text-[clamp(2rem,5.2vw,52px)] leading-[1.02] font-black tracking-[-0.03em] uppercase">
          {humanize(stage1.regime)}
          <span className="text-paper/45"> / {stage1.strength}</span>
        </h2>

        <p className="mt-6 max-w-[62ch] text-[17px] leading-[1.6] text-paper/85">
          {stage1.summary}
        </p>

        <ol className="mt-8 flex flex-col gap-2 border-t border-rule-navy pt-6">
          {stage2.reasoning_chain.map((step, i) => (
            <li key={i} className="flex gap-4">
              <span className="label mt-[3px] shrink-0 text-paper/45">
                {String(i + 1).padStart(2, "0")}
              </span>
              <span className="text-[15px] leading-[1.5] text-paper/75">{step}</span>
            </li>
          ))}
        </ol>
      </div>

      {/* four-column key levels, ruled off from the diagnosis above */}
      <div className="grid grid-cols-2 border-t border-rule-navy sm:grid-cols-4">
        {keyLevels(analysis).map((lv, i) => (
          <div
            key={lv.label}
            className={[
              "px-8 py-7 sm:px-12",
              "border-rule-navy",
              i > 0 ? "sm:border-l" : "",
              i % 2 === 1 ? "border-l sm:border-l" : "",
              i < 2 ? "border-b sm:border-b-0" : "",
            ].join(" ")}
          >
            <div className="label text-paper/55">{lv.label}</div>
            <div className="num mt-3 text-[26px] leading-none font-medium tracking-[0.01em]">
              {lv.value}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}
