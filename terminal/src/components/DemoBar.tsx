import { BookOpen, X } from "lucide-react";
import Button from "./ui/Button";
import { formatDayTime } from "../lib/timezone";
import type { Zone } from "../lib/timezone";
import type { DemoSample, DemoSampleSummary, DemoStatus } from "../api/types";

interface Props {
  samples: DemoSampleSummary[];
  /** The sample currently on screen, if any. */
  active: DemoSample | null;
  onLoad: (id: string) => void;
  onClear: () => void;
  loading: boolean;
  /** Null until the first status call returns. */
  status: DemoStatus | null;
  usingOwnKey: boolean;
  zone: Zone;
}

/**
 * The two demo layers, said plainly.
 *
 * A stored sample is real output this system produced, frozen to a file.
 * It is never live and never claims to be: while one is on screen this bar
 * says so, in the present tense, with the date of the bar it was made on.
 *
 * The remaining-run counter exists so an exhausted budget is never a
 * surprise. A visitor who has already spent it should learn that before
 * pressing Analyze, not from an error afterwards.
 */
export default function DemoBar({
  samples, active, onLoad, onClear, loading, status, usingOwnKey, zone,
}: Props) {
  if (!samples.length && !active) return null;

  // With their own key the budget does not apply, and a mock server is not
  // spending anything - in neither case is there a quota worth showing.
  const showQuota = !usingOwnKey && status?.metered === true;
  const remaining = status?.remaining ?? 0;

  if (active) {
    return (
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-border
                      bg-ctl px-4 py-2">
        <BookOpen size={15} className="shrink-0 text-bull" />
        <span className="font-sans text-[13px] text-text">
          Stored example — <span className="num">{active.symbol} {active.interval}</span>,
          analysed {formatDayTime(active.bar_ts, zone)}.
        </span>
        <span className="font-sans text-[13px] text-muted">
          Not live. Nothing on screen is updating.
        </span>
        <span className="ml-auto">
          <Button onClick={onClear}>
            <X size={16} />
            Back to live
          </Button>
        </span>
      </div>
    );
  }

  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-border px-4 py-2">
      <BookOpen size={15} className="shrink-0 text-muted" />
      <span className="font-sans text-[13px] text-muted">
        New here? Load a stored example — free, no key needed:
      </span>
      {samples.map((s) => (
        <Button key={s.id} onClick={() => onLoad(s.id)} disabled={loading}>
          {s.symbol} {s.interval}
        </Button>
      ))}

      {showQuota && (
        <span className="ml-auto font-sans text-[13px] text-muted">
          {remaining > 0 ? (
            <>
              <span className="num text-text">{remaining}</span> free{" "}
              {remaining === 1 ? "analysis" : "analyses"} left today
            </>
          ) : (
            <>Daily demo budget used — add your own key in Settings</>
          )}
        </span>
      )}
    </div>
  );
}
