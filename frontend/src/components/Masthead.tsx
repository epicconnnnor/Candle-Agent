export default function Masthead({
  headline,
  subhead,
}: {
  headline: string;
  subhead: string;
}) {
  return (
    <header className="pt-16 pb-20">
      <h1 className="text-[clamp(3.25rem,11.5vw,120px)] leading-[0.86] font-black tracking-[-0.04em] uppercase">
        {headline}
      </h1>
      <p className="mt-8 max-w-[46ch] text-[19px] leading-[1.55] text-ink/80">
        {subhead}
      </p>
    </header>
  );
}
