import { AlertTriangle, Clock, Info } from "lucide-react";
import type { IngestStatus } from "../api/types";
import { formatDayTime } from "../lib/timezone";
import type { Zone } from "../lib/timezone";

interface Props {
  problem: IngestStatus | null;
  market: IngestStatus | null;
  backfill: IngestStatus | null;
  zone: Zone;
}

/** "2026-08-28T09:30:00-04:00" -> "Fri 09:30 EDT" in the chosen zone. */
function whenOpen(iso: string | null | undefined, zone: Zone): string {
  if (!iso) return "an unannounced time";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return formatDayTime(d.getTime(), zone);
}

const KIND_LABEL: Record<string, string> = {
  region_blocked: "Region blocked",
  unknown_symbol: "Unknown symbol",
  auth: "Authentication failed",
  reconnecting: "Reconnecting",
  stalled: "Feed stalled",
  unavailable: "Source unavailable",
  closed: "Stream closed",
};

function problemLabel(s: IngestStatus): string {
  if (s.kind && KIND_LABEL[s.kind]) return KIND_LABEL[s.kind];
  if (s.state === "backfill_failed") return "History unavailable";
  return "Feed problem";
}

/**
 * Everything the backend says about the feed, rendered where it cannot be
 * missed. An empty chart with no explanation is the failure mode this
 * exists to prevent, so each status class gets its own visible treatment.
 */
export default function StatusBanner({ problem, market, backfill, zone }: Props) {
  if (!problem && !market && !backfill) return null;

  return (
    <div className="flex flex-col gap-2">
      {problem && (
        <div
          role="alert"
          className="flex items-start gap-2.5 rounded-lg border border-bear bg-bear/12 px-4 py-2.5"
        >
          <AlertTriangle size={15} className="mt-0.5 shrink-0 text-bear" />
          <div className="min-w-0">
            <span className="lbl text-bear">{problemLabel(problem)}</span>
            <p className="mt-1 text-[13px] leading-snug text-text">
              {problem.message ?? problem.reason ?? "The feed reported a failure."}
            </p>
            {(problem.attempts || problem.code) && (
              <p className="num mt-1 text-[12px] text-muted">
                {problem.code != null && <>code {problem.code}</>}
                {problem.code != null && problem.attempts != null && " · "}
                {problem.attempts != null && <>{problem.attempts} consecutive attempts</>}
                {problem.retry_in_s != null && <> · retrying in {problem.retry_in_s}s</>}
              </p>
            )}
          </div>
        </div>
      )}

      {market && (
        <div className="flex items-center gap-2.5 rounded-lg border border-ctl-border bg-ctl px-4 py-2.5">
          <Clock size={15} className="shrink-0 text-muted" />
          <p className="text-[13px] text-muted">
            <span className="text-ctl-text">Market closed.</span> Showing stored
            history — live bars resume at{" "}
            <span className="num text-ctl-text">{whenOpen(market.next_open, zone)}</span>.
          </p>
        </div>
      )}

      {backfill?.partial && (
        <div className="flex items-center gap-2.5 px-1">
          <Info size={13} className="shrink-0 text-muted" />
          <p className="text-[12px] text-muted">
            <span className="num text-ctl-text">{backfill.bars}</span> of{" "}
            <span className="num">{backfill.requested}</span>{" "}
            {backfill.interval} bars available — the source returned all the
            history it has at this interval.
          </p>
        </div>
      )}
    </div>
  );
}
