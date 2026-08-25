interface Item {
  label: string;
  value: string;
}

export default function MetaStrip({ items }: { items: Item[] }) {
  return (
    <dl className="grid grid-cols-2 gap-y-6 border-b border-dashed border-ink py-5 sm:grid-cols-4">
      {items.map((it) => (
        <div key={it.label} className="flex flex-col gap-2">
          <dt className="label">{it.label}</dt>
          <dd className="label-ink">{it.value}</dd>
        </div>
      ))}
    </dl>
  );
}
