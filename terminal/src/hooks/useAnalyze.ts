import { useCallback, useEffect, useRef, useState } from "react";

/**
 * The Analyze button is the status indicator - there is no separate spinner.
 * The label cycles Analyze -> Preparing... -> Analyzing... -> Stop, and the
 * button is filled bull while idle, bear for every running phase. Clicking
 * at any point during a run aborts it.
 */
export type Phase = "idle" | "preparing" | "analyzing" | "cancellable";

const LABEL: Record<Phase, string> = {
  idle: "Analyze",
  preparing: "Preparing...",
  analyzing: "Analyzing...",
  cancellable: "Stop",
};

const PREPARING_MS = 700;
const ANALYZING_MS = 1800;
const CANCELLABLE_MS = 1500;

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
      window.setTimeout(() => setPhase("cancellable"), PREPARING_MS + ANALYZING_MS),
      window.setTimeout(() => {
        setPhase("idle");
        onComplete();
      }, PREPARING_MS + ANALYZING_MS + CANCELLABLE_MS)
    );
  }, [phase, clear, onComplete]);

  return { phase, label: LABEL[phase], running: phase !== "idle", toggle };
}
