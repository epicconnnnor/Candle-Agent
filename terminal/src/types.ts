/**
 * App-level types.
 *
 * Everything that mirrors the backend now lives in api/types.ts, generated
 * from the Python. This module re-exports what components use and adds the
 * few types that are purely local to the UI.
 */
export type {
  AssetClass,
  Bar,
  BarRow,
  Confidence,
  ConnectionState,
  Decision,
  IngestStatus,
  Interval,
  Regime,
  Stage1,
  Stage2,
  Strength,
  SymbolInfo,
} from "./api/types";

export interface ChatMessage {
  id: number;
  role: "user" | "agent";
  text: string;
}
