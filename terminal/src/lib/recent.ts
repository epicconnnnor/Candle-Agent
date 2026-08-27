/**
 * Recently picked symbols, remembered per browser.
 *
 * Shared by the picker (which pins them to the top of the list) and the
 * app (which opens on the most recent one), so a reload does not throw you
 * back to the default symbol.
 *
 * localStorage throws outright in some contexts - private windows, blocked
 * site data - so every access is guarded and an empty list is a valid answer.
 */
const KEY = "candle-agent.recent-symbols";

export const MAX_RECENT = 5;

export function loadRecent(): string[] {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    return Array.isArray(parsed)
      ? parsed.filter((s): s is string => typeof s === "string").slice(0, MAX_RECENT)
      : [];
  } catch {
    return [];
  }
}

export function pushRecent(symbol: string, current: string[]): string[] {
  const next = [symbol, ...current.filter((s) => s !== symbol)].slice(0, MAX_RECENT);
  try {
    localStorage.setItem(KEY, JSON.stringify(next));
  } catch {
    /* a remembered list is a convenience, never a requirement */
  }
  return next;
}
