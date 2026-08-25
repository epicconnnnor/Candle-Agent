import type { ReactNode } from "react";

/** Panels get 8px corners; tables and their containers stay square. */
export default function Panel({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`rounded-lg border border-border bg-panel ${className}`}>
      {children}
    </section>
  );
}
