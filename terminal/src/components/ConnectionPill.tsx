import type { ConnectionState } from "../api/types";

const LOOK: Record<ConnectionState, { label: string; dot: string; text: string }> = {
  connected: { label: "Connected", dot: "bg-bull", text: "text-muted" },
  connecting: { label: "Connecting", dot: "bg-muted", text: "text-muted" },
  reconnecting: { label: "Reconnecting", dot: "bg-bear", text: "text-ctl-text" },
  disconnected: { label: "Disconnected", dot: "bg-bear", text: "text-bear" },
};

/** SSE state, next to the Live toggle. Pulses while it is trying to recover. */
export default function ConnectionPill({ state }: { state: ConnectionState }) {
  const look = LOOK[state];
  const busy = state === "reconnecting" || state === "connecting";

  return (
    <span
      role="status"
      aria-live="polite"
      title={`Event stream: ${look.label.toLowerCase()}`}
      className="inline-flex h-[34px] shrink-0 items-center gap-2 px-2"
    >
      <span
        aria-hidden="true"
        className={`h-1.5 w-1.5 rounded-full ${look.dot} ${busy ? "animate-pulse" : ""}`}
      />
      <span className={`lbl ${look.text}`}>{look.label}</span>
    </span>
  );
}
