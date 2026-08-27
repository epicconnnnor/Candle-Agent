import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown, Search } from "lucide-react";
import type { SymbolInfo } from "../api/types";
import { loadRecent, pushRecent } from "../lib/recent";

const MAX_VISIBLE = 50;

interface Props {
  symbol: string;
  symbols: SymbolInfo[];
  loading?: boolean;
  error?: string | null;
  onSelect: (info: SymbolInfo) => void;
}

interface Group {
  label: string;
  items: SymbolInfo[];
}

const CLASS_LABEL: Record<string, string> = { equity: "Stocks", crypto: "Crypto" };

/** Searchable symbol combobox. No library: an input, a list, and key handling. */
export default function SymbolPicker({ symbol, symbols, loading, error, onSelect }: Props) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const [recent, setRecent] = useState<string[]>(loadRecent);

  const box = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  const { groups, flat, total } = useMemo(() => {
    const q = query.trim().toLowerCase();
    const matches = q
      ? symbols.filter(
          (s) =>
            s.symbol.toLowerCase().includes(q) || s.name.toLowerCase().includes(q),
        )
      : symbols;

    const capped = matches.slice(0, MAX_VISIBLE);
    const byClass = new Map<string, SymbolInfo[]>();
    for (const s of capped) {
      const list = byClass.get(s.asset_class) ?? [];
      list.push(s);
      byClass.set(s.asset_class, list);
    }

    const out: Group[] = [];
    // recent is pinned above everything, and only when not searching
    if (!q && recent.length) {
      const pinned = recent
        .map((r) => symbols.find((s) => s.symbol === r))
        .filter((s): s is SymbolInfo => Boolean(s));
      if (pinned.length) out.push({ label: "Recent", items: pinned });
    }
    for (const [cls, items] of byClass) {
      out.push({ label: CLASS_LABEL[cls] ?? cls, items });
    }
    return { groups: out, flat: out.flatMap((g) => g.items), total: matches.length };
  }, [symbols, query, recent]);

  useEffect(() => setActive(0), [query, open]);

  // click outside cancels without selecting
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!box.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  useEffect(() => {
    if (open) input.current?.focus();
    else setQuery("");
  }, [open]);

  // keep the active row in view while arrowing through a long list
  useEffect(() => {
    listRef.current
      ?.querySelector(`[data-idx="${active}"]`)
      ?.scrollIntoView({ block: "nearest" });
  }, [active]);

  const choose = (info: SymbolInfo) => {
    setRecent((current) => pushRecent(info.symbol, current));
    setOpen(false);
    onSelect(info);
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") {
      e.preventDefault();
      setOpen(false);
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((i) => Math.min(i + 1, flat.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((i) => Math.max(i - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (flat[active]) choose(flat[active]);
    }
  };

  let idx = -1;

  return (
    <div ref={box} className="relative">
      <button
        type="button"
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className="inline-flex h-[34px] items-center gap-2 rounded-lg border border-ctl-border
                   bg-ctl px-3 font-mono text-[13px] tracking-[0.12em] text-ctl-text uppercase
                   transition-colors hover:border-ctl-border-hover hover:bg-ctl-hover
                   hover:text-text focus-visible:ring-1 focus-visible:ring-muted
                   focus-visible:outline-none"
      >
        {symbol}
        <ChevronDown size={14} className="text-muted" />
      </button>

      {open && (
        <div
          className="absolute top-[38px] left-0 z-30 w-[320px] overflow-hidden rounded-lg
                     border border-ctl-border bg-panel shadow-lg shadow-black/40"
        >
          <div className="flex items-center gap-2 border-b border-border px-3">
            <Search size={14} className="shrink-0 text-muted" />
            <input
              ref={input}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={onKeyDown}
              placeholder="Search symbol or name"
              aria-label="Search symbols"
              className="h-[36px] w-full bg-transparent font-mono text-[13px] text-text
                         placeholder:text-muted focus:outline-none"
            />
          </div>

          <div ref={listRef} role="listbox" className="max-h-[320px] overflow-y-auto py-1">
            {loading && <p className="px-3 py-3 text-[13px] text-muted">Loading symbols…</p>}
            {error && !loading && <p className="px-3 py-3 text-[13px] text-bear">{error}</p>}
            {!loading && !error && flat.length === 0 && (
              <p className="px-3 py-3 text-[13px] text-muted">No match for “{query}”.</p>
            )}

            {groups.map((g) => (
              <div key={g.label}>
                <div className="lbl px-3 pt-2 pb-1">{g.label}</div>
                {g.items.map((s) => {
                  idx += 1;
                  const i = idx;
                  return (
                    <button
                      key={`${g.label}-${s.source}-${s.symbol}`}
                      data-idx={i}
                      role="option"
                      aria-selected={s.symbol === symbol}
                      onMouseEnter={() => setActive(i)}
                      onClick={() => choose(s)}
                      className={`flex w-full items-baseline gap-2 px-3 py-1.5 text-left
                                  ${i === active ? "bg-ctl-hover" : ""}`}
                    >
                      <span className="num w-[92px] shrink-0 truncate text-[13px] text-text">
                        {s.symbol}
                      </span>
                      <span className="truncate text-[12px] text-muted">{s.name}</span>
                      <span className="lbl ml-auto shrink-0">{s.source}</span>
                    </button>
                  );
                })}
              </div>
            ))}

            {total > MAX_VISIBLE && (
              <p className="border-t border-border px-3 py-2 text-[12px] text-muted">
                {total.toLocaleString()} matches — showing {MAX_VISIBLE}. Keep typing to narrow.
              </p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
