import NavBar from "./components/NavBar";
import MetaStrip from "./components/MetaStrip";
import Masthead from "./components/Masthead";
import DiagnosisBlock from "./components/DiagnosisBlock";
import FactorCards from "./components/FactorCards";
import BarTable from "./components/BarTable";
import { analysis, bars, factors, fmtStamp, humanize } from "./data";

/** Section heading: wide-tracked mono label over a hairline rule. */
function SectionRule({ children }: { children: string }) {
  return (
    <div className="mb-8 border-t border-ink pt-4">
      <span className="label">{children}</span>
    </div>
  );
}

export default function App() {
  const { stage1, stage2 } = analysis;
  const last = bars[bars.length - 1];

  return (
    <div className="min-h-screen">
      <NavBar />

      <main className="mx-auto max-w-[1040px] px-6">
        <MetaStrip
          items={[
            { label: "Symbol", value: analysis.symbol },
            { label: "Timeframe", value: analysis.timeframe },
            { label: "Last update", value: fmtStamp(analysis.ts) },
            { label: "Bars loaded", value: String(bars.length) },
          ]}
        />

        <Masthead
          headline={`${analysis.symbol} ${analysis.timeframe}`}
          subhead={`The model reads a ${humanize(stage1.regime)} of ${
            stage1.strength
          } conviction and returns a ${humanize(
            stage2.decision
          )} at ${stage2.entry.toFixed(2)}, risking ${(
            stage2.entry - stage2.stop
          ).toFixed(2)} points to make ${(stage2.target - stage2.entry).toFixed(2)}.`}
        />

        <DiagnosisBlock analysis={analysis} />

        <section className="pt-24">
          <SectionRule>Read&#8209;outs</SectionRule>
          <FactorCards factors={factors} />
        </section>

        <section className="pt-24 pb-8">
          <SectionRule>Recent bars</SectionRule>
          <BarTable bars={bars} />
        </section>
      </main>

      <footer className="mt-16 border-t border-ink">
        <div className="mx-auto flex max-w-[1040px] flex-wrap items-center justify-between gap-4 px-6 py-7">
          <span className="label">
            Analysis only &middot; never places orders
          </span>
          <span className="label">
            Last close {last.close.toFixed(2)} &middot; {analysis.latency_ms}&thinsp;ms &middot;{" "}
            {stage2.confidence} confidence
          </span>
        </div>
      </footer>
    </div>
  );
}
