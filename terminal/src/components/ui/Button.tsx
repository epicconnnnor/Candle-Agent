import type { ButtonHTMLAttributes } from "react";

type Variant =
  | "default"  // secondary: visible but quiet
  | "primary"  // the one filled action
  | "danger"   // primary action in its stop state
  | "toggle"   // state, off
  | "active"   // state, on
  | "ghost";   // icon-only / lowest weight

const VARIANTS: Record<Variant, string> = {
  default:
    "bg-ctl border border-ctl-border text-ctl-text " +
    "hover:bg-ctl-hover hover:border-ctl-border-hover hover:text-text",
  primary:
    "bg-bull border-0 font-medium text-base hover:bg-bull-hover",
  danger:
    "bg-bear border-0 font-medium text-base hover:bg-bear/90",
  toggle:
    "bg-transparent border border-ctl-border text-muted " +
    "hover:border-ctl-border-hover hover:text-ctl-text",
  active:
    "bg-bull/12 border border-bull text-bull hover:bg-bull/20",
  ghost:
    "bg-transparent border-0 text-muted hover:bg-ctl hover:text-text",
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
        "inline-flex h-[34px] shrink-0 items-center justify-center gap-2",
        "rounded-lg px-3 font-mono text-[13px] tracking-[0.12em] uppercase",
        "transition-colors disabled:cursor-not-allowed disabled:opacity-40",
        "focus-visible:ring-1 focus-visible:ring-muted focus-visible:outline-none",
        VARIANTS[variant],
        className,
      ].join(" ")}
    />
  );
}
