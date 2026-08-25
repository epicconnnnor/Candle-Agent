interface Props {
  value: string;
  options: string[];
  onChange: (v: string) => void;
  label: string;
}

export default function Select({ value, options, onChange, label }: Props) {
  return (
    <label className="flex items-center gap-2">
      <span className="sr-only">{label}</span>
      <select
        value={value}
        aria-label={label}
        onChange={(e) => onChange(e.target.value)}
        className="lbl h-8 rounded-md border border-border bg-panel px-2 pr-6 text-text
                   hover:border-muted/60 focus-visible:ring-1 focus-visible:ring-muted
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
