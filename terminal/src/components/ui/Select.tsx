interface Props {
  value: string;
  options: string[];
  onChange: (v: string) => void;
  label: string;
}

/** Matches the secondary button treatment so the toolbar reads as one row. */
export default function Select({ value, options, onChange, label }: Props) {
  return (
    <label className="flex items-center gap-2">
      <span className="sr-only">{label}</span>
      <select
        value={value}
        aria-label={label}
        onChange={(e) => onChange(e.target.value)}
        className="h-[34px] rounded-lg border border-ctl-border bg-ctl px-3 pr-7
                   font-mono text-[13px] tracking-[0.12em] text-ctl-text uppercase
                   transition-colors hover:border-ctl-border-hover hover:bg-ctl-hover
                   hover:text-text focus-visible:ring-1 focus-visible:ring-muted
                   focus-visible:outline-none"
      >
        {options.map((o) => (
          <option key={o} value={o} className="bg-panel text-text">
            {o}
          </option>
        ))}
      </select>
    </label>
  );
}
