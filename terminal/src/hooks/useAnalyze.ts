import { useCallback, useEffect, useRef, useState } from "react";

/**
 * The Analyze button is the status indicator - there is no separate spinner.
 * idle -> preparing -> analyzing -> idle, and a click mid-run aborts.
 */
export type Phase = "idle" | "preparing" | "analyzing";

const LABEL: Record<Phase, string> = {
  idle: "Analyze",
  preparing: "Preparing...",
  analyzing: "Analyzing...",
};

const PREPARING_MS = 900;
const ANALYZING_MS = 2400;

export function useAnalyze(onComplete: () => void) {
  const [phase, setPhase] = useState<Phase>("idle");
  const timers = useRef<number[]>([]);

  const clear = useCallback(() => {
    timers.current.forEach(clearTimeout);
    timers.current = [];
  }, []);

  useEffect(() => clear, [clear]);

  const toggle = useCallback(() => {
    if (phase !== "idle") {
      clear();
      setPhase("idle");
      return;
    }
    setPhase("preparing");
    timers.current.push(
      window.setTimeout(() => setPhase("analyzing"), PREPARING_MS),
      window.setTimeout(() => {
        setPhase("idle");
        onComplete();
      }, PREPARING_MS + ANALYZING_MS)
    );
  }, [phase, clear, onComplete]);

  // running label is "Stop" on hover-intent; the raw phase label otherwise
  return { phase, label: LABEL[phase], running: phase !== "idle", toggle };
}
