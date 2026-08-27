import Card, { CardRow, CardRows } from "./ui/Card";
import type { ConnectionState } from "../api/types";

const CONNECTION_LABEL: Record<ConnectionState, string> = {
  connected: "Connected",
  connecting: "Connecting",
  reconnecting: "Reconnecting",
  disconnected: "Disconnected",
};

const CONNECTION_TONE: Record<ConnectionState, string> = {
  connected: "text-bull",
  connecting: "text-muted",
  reconnecting: "text-text",
  disconnected: "text-bear",
};

interface Props {
  connection: ConnectionState;
  usingOwnKey: boolean;
  source: string;
  /** Epoch SECONDS of the newest bar, or null before any arrive. */
  lastBarTime: number | null;
}

export default function SessionCard({
  connection, usingOwnKey, source, lastBarTime,
}: Props) {
  const updated =
    lastBarTime === null
      ? "—"
      : new Date(lastBarTime * 1000).toISOString().slice(11, 19) + " UTC";

  return (
    <Card title="Session">
      <CardRows>
        <CardRow label="Stream" valueClassName={CONNECTION_TONE[connection]}>
          {CONNECTION_LABEL[connection]}
        </CardRow>
        <CardRow label="Source">{source}</CardRow>
        <CardRow
          label="API key"
          valueClassName={usingOwnKey ? "text-bull" : "text-text"}
        >
          {usingOwnKey ? "Yours" : "Server"}
        </CardRow>
        <CardRow label="Last update">{updated}</CardRow>
      </CardRows>
    </Card>
  );
}
