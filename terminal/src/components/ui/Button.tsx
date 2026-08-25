import type { ButtonHTMLAttributes } from "react";

type Variant = "default" | "primary" | "ghost" | "active";

const VARIANTS: Record<Variant, string> = {
  default:
    "border-border bg-panel text-text hover:border-muted/60 hover:bg-grid",
  primary:
    "border-bull/60 bg-bull/15 text-bull hover:bg-bull/25 hover:border-bull",
  ghost:
    "border-transparent bg-transparent text-muted hover:text-text hover:bg-panel",
  active:
    "border-bull bg-bull/20 text-bull",
};

interface Props extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
}

export default function Button({
  variant = "default",
  className = "",
  ...rest
}: Props) {
  return (
    <button
      {...rest}
      className={[
        "lbl inline-flex h-8 shrink-0 items-center justify-center gap-2 rounded-md border px-3 transition-colors",
        "disabled:cursor-not-allowed disabled:opacity-40",
        "focus-visible:ring-1 focus-visible:ring-muted focus-visible:outline-none",
        VARIANTS[variant],
        className,
      ].join(" ")}
    />
  );
}
