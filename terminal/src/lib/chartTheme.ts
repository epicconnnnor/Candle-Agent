import type { UTCTimestamp } from "lightweight-charts";
import type { Bar } from "../api/types";

/** Chart colours, mirroring the tokens in index.css. */
export const C = {
  base: "#0E1116",
  grid: "#181D25",
  muted: "#6B7686",
  bull: "#26A69A",
  bear: "#EF5350",
  support: "#1E8E7E",
  resist: "#C0392B",
};

export const toCandle = (b: Bar) => ({
  time: b.time as UTCTimestamp,
  open: b.open,
  high: b.high,
  low: b.low,
  close: b.close,
});
