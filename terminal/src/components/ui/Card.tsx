import type { ReactNode } from "react";

/**
 * The one container pattern every module uses.
 *
 * Panel background, a single 1px boundary, 10px radius, 20px padding, and
 * a title row divided from the body. Nothing inside a card draws another
 * border - one boundary per card is what makes the modules readable as
 * discrete objects rather than one continuous wall.
 */
export default function Card({
  title,
  action,
  children,
  bodyClassName = "",
  className = "",
}: {
  title: ReactNode;
  /** Optional control on the title row, e.g. a button. */
  action?: ReactNode;
  children: ReactNode;
  bodyClassName?: string;
  className?: string;
}) {
  return (
    <section
      className={`rounded-[10px] border border-border bg-panel p-5 ${className}`}
    >
      <div className="flex min-h-[24px] items-center justify-between gap-4 border-b border-border pb-3">
        <h2 className="font-sans text-[13px] font-medium text-muted">{title}</h2>
        {action}
      </div>
      <div className={`mt-4 ${bodyClassName}`}>{children}</div>
    </section>
  );
}

/**
 * A label/value line inside a card: label left in muted, value right in
 * monospace. Stacked rows get a 12px rhythm from `CardRows`.
 */
export function CardRow({
  label,
  children,
  valueClassName = "",
}: {
  label: ReactNode;
  children: ReactNode;
  valueClassName?: string;
}) {
  return (
    <div className="flex items-baseline justify-between gap-4">
      <span className="font-sans text-[13px] text-muted">{label}</span>
      <span className={`num text-[13px] text-text ${valueClassName}`}>{children}</span>
    </div>
  );
}

/** 12px vertical rhythm between rows inside a card. */
export function CardRows({ children }: { children: ReactNode }) {
  return <div className="flex flex-col gap-3">{children}</div>;
}
