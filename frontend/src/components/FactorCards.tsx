import type { Factor } from "../data";

function Card({ factor }: { factor: Factor }) {
  return (
    <article className="flex flex-col border border-dashed border-ink p-7">
      <h3 className="label">{factor.key}</h3>

      <p className="mt-5 text-[22px] leading-[1.15] font-bold tracking-[-0.02em]">
        {factor.headline}
      </p>

      <p className="mt-4 text-[15px] leading-[1.5] text-ink/70">{factor.detail}</p>

      <dl className="mt-auto flex flex-col gap-0 pt-7">
        {factor.readings.map((r) => (
          <div
            key={r.label}
            className="flex items-center justify-between border-t border-rule py-3"
          >
            <dt className="label">{r.label}</dt>
            <dd className="num text-[12px] font-medium">{r.value}</dd>
          </div>
        ))}
      </dl>
    </article>
  );
}

export default function FactorCards({ factors }: { factors: Factor[] }) {
  return (
    <div className="grid grid-cols-1 gap-6 sm:grid-cols-3">
      {factors.map((f) => (
        <Card key={f.key} factor={f} />
      ))}
    </div>
  );
}
