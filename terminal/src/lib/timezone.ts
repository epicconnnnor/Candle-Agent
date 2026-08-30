/**
 * The one place a timestamp becomes a string.
 *
 * Display only. Bars, analyses, replay runs and scores are stored as UTC
 * epoch milliseconds and stay that way; the bar table sent to the model
 * stays UTC too, so an analysis means the same thing regardless of who
 * ran it or where they were sitting. Nothing here touches a payload.
 *
 * Zones are IANA names rather than fixed offsets, so Intl applies the
 * right DST rules for the instant being formatted rather than for today.
 * That matters for replayed history: a bar from January formats as EST
 * and one from August as EDT, which a stored `-05:00` could not do.
 */
export const ZONES = ["local", "UTC", "America/New_York"] as const;

export type Zone = (typeof ZONES)[number];

export const ZONE_LABELS: Record<Zone, string> = {
  local: "Local (browser)",
  UTC: "UTC",
  "America/New_York": "America/New_York — US market time",
};

const STORAGE_KEY = "candle-agent.timezone";

/** `undefined` means "whatever the browser is", which is what Intl wants. */
function ianaOf(zone: Zone): string | undefined {
  return zone === "local" ? undefined : zone;
}

export function loadZone(): Zone {
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    return (ZONES as readonly string[]).includes(stored ?? "")
      ? (stored as Zone)
      : "local";
  } catch {
    return "local";
  }
}

export function storeZone(zone: Zone): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, zone);
  } catch {
    /* storage refused: the choice still applies for this session */
  }
}

// Intl.DateTimeFormat construction is not free and these run per bar and
// per axis tick, so formatters are built once per shape and reused.
const cache = new Map<string, Intl.DateTimeFormat>();

function formatter(zone: Zone, opts: Intl.DateTimeFormatOptions): Intl.DateTimeFormat {
  const key = `${zone}|${JSON.stringify(opts)}`;
  let f = cache.get(key);
  if (!f) {
    // en-US, but only for the zone abbreviation: it is the locale that
    // renders America/New_York as "EDT" rather than "GMT-4", which is the
    // whole point of showing it. h23 then pins 24-hour display, because
    // this is a trading terminal and en-US would otherwise say "4:07 PM".
    f = new Intl.DateTimeFormat("en-US", {
      hourCycle: "h23",
      timeZone: ianaOf(zone),
      ...opts,
    });
    cache.set(key, f);
  }
  return f;
}

/** The zone's abbreviation AT THIS INSTANT — "UTC", "EDT", "EST", "GMT+1". */
export function zoneAbbrev(ms: number, zone: Zone): string {
  const parts = formatter(zone, {
    hour: "2-digit",
    timeZoneName: "short",
  }).formatToParts(new Date(ms));
  return parts.find((p) => p.type === "timeZoneName")?.value ?? "";
}

/** "20:07:00 UTC" — the abbreviation is never omitted, so it cannot be read wrong. */
export function formatTime(ms: number, zone: Zone, seconds = true): string {
  const time = formatter(zone, {
    hour: "2-digit",
    minute: "2-digit",
    ...(seconds ? { second: "2-digit" } : {}),
  }).format(new Date(ms));
  return `${time} ${zoneAbbrev(ms, zone)}`;
}

/** "Fri 09:30 EDT" — for a time far enough away that the day matters. */
export function formatDayTime(ms: number, zone: Zone): string {
  const text = formatter(zone, {
    weekday: "short",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(ms));
  return `${text} ${zoneAbbrev(ms, zone)}`;
}

/**
 * "20:07" — bare, for chart axis ticks.
 *
 * The only formatter here that omits the abbreviation, because repeating
 * it on every tick would be noise. The zone is stated once, unambiguously,
 * in the OHLC strip directly above the chart.
 */
export function formatAxisTime(ms: number, zone: Zone): string {
  return formatter(zone, { hour: "2-digit", minute: "2-digit" }).format(new Date(ms));
}

/** "28 Aug 20:07 EDT" — chart crosshair, where one value has room to be explicit. */
export function formatCrosshair(ms: number, zone: Zone): string {
  const text = formatter(zone, {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(ms));
  return `${text} ${zoneAbbrev(ms, zone)}`;
}
