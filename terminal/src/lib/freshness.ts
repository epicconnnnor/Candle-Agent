/**
 * Is the analysis on screen still a description of the market on screen?
 *
 * An analysis names levels against one price. Once price has walked far
 * enough from that, those levels are history rather than advice. "Far
 * enough" is measured in ATRs so it adapts to the instrument.
 */

/** Price drift, in ATRs, past which an analysis is stale. */
export const STALE_ATR_MULTIPLE = 2;

export type Freshness =
  | { state: "fresh"; driftAtr: number }
  | { state: "stale"; driftAtr: number }
  | { state: "unknown" };

export function freshnessOf(opts: {
  analysisPrice: number | null;
  analysisAtr: number | null;
  currentPrice: number | null;
  currentAtr: number;
}): Freshness {
  const { analysisPrice, analysisAtr, currentPrice, currentAtr } = opts;

  // Rows written before price_at existed carry null. Saying "fresh" there
  // would be a claim we cannot support, so it is reported as unknown.
  if (analysisPrice === null || currentPrice === null) return { state: "unknown" };

  // Prefer the ATR recorded with the analysis: it is the volatility regime
  // the verdict was formed in. Fall back to the live one for older rows.
  const atr = analysisAtr && analysisAtr > 0 ? analysisAtr : currentAtr;
  if (!(atr > 0)) return { state: "unknown" };

  const driftAtr = Math.abs(currentPrice - analysisPrice) / atr;
  return driftAtr > STALE_ATR_MULTIPLE
    ? { state: "stale", driftAtr }
    : { state: "fresh", driftAtr };
}
