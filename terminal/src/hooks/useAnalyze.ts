import { useCallback, useEffect, useRef, useState } from "react";
import { requestAnalysis } from "../api/client";

/**
 * The Analyze button is the status indicator - there is no separate spinner.
 * The label cycles Analyze -> Requesting... -> Analyzing... -> Stop.
 *
 * POST /api/analyze is fire-and-forget (202): the analyzer publishes the
 * result on the bus and it reaches us over SSE, so `settle()` is what ends
 * the run. Stop only detaches this button - the backend has no cancel.
 */
export type Phase = "idle" | "requesting" | "analyzing";

const LABEL: Record<Phase, string> = {
  idle: "Analyze",
  requesting: "Requesting...",
  analyzing: "Stop",
};

/** Give up waiting for an SSE result rather than spinning forever. */
const TIMEOUT_MS = 120_000;

export function useAnalyze(
  symbol: string,
  apiKey?: string,
  onError?: (message: string) => void,
) {
  const [phase, setPhase] = useState<Phase>("idle");
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clear = useCallback(() => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
  }, []);

  useEffect(() => clear, [clear]);
  useEffect(() => {
    setPhase("idle");                 // a symbol change abandons the old run
    clear();
  }, [symbol, clear]);

  /** Called when an analysis for this symbol arrives on the stream. */
  const settle = useCallback(() => {
    clear();
    setPhase("idle");
  }, [clear]);

  const toggle = useCallback(async () => {
    if (phase !== "idle") {
      clear();
      setPhase("idle");
      return;
    }
    setPhase("requesting");
    try {
      const res = await requestAnalysis(symbol, apiKey);
      if ("status" in res && res.status === "completed") {
        // a visitor key runs inline, so the result is already here and
        // there is no SSE event to wait for
        setPhase("idle");
        return;
      }
      setPhase("analyzing");
      timer.current = setTimeout(() => setPhase("idle"), TIMEOUT_MS);
    } catch (e) {
      setPhase("idle");
      onError?.(e instanceof Error ? e.message : String(e));
    }
  }, [phase, symbol, apiKey, clear, onError]);

  return { phase, label: LABEL[phase], running: phase !== "idle", toggle, settle };
}
