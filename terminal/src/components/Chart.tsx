import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useRef,
} from "react";
import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  LineStyle,
  createChart,
  type IChartApi,
  type ISeriesApi,
  type IPriceLine,
  type AutoscaleInfo,
} from "lightweight-charts";
import type { Bar, Stage1, Stage2 } from "../types";
import { C, toCandle } from "../lib/chartTheme";

/** Imperative handle so a live stream can push bars without a re-render. */
export interface ChartHandle {
  update: (bar: Bar) => void;
  resetZoom: () => void;
}

interface Props {
  bars: Bar[];
  stage1: Stage1 | null;
  stage2: Stage2 | null;
  /** Bumped whenever the series is replaced wholesale (new symbol,
   *  new interval, fresh backfill). Live ticks go through update()
   *  instead, so a bar arriving never re-sets the whole series. */
  revision: number;
  /**
   * Whether the decision levels may widen the price scale.
   *
   * Only for an analysis that still describes this market. A stale or
   * unknown-age one keeps its lines - they are what it said - but must not
   * drag the axis out to reach them, which squashes the candles into a
   * band and is the distortion this guards against.
   */
  scaleToLevels?: boolean;
}

const Chart = forwardRef<ChartHandle, Props>(function Chart(
  { bars, stage1, stage2, revision, scaleToLevels = true },
  ref
) {
  const host = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const series = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const lines = useRef<IPriceLine[]>([]);
  // levels currently drawn; read by autoscaleInfoProvider so they stay on-screen
  const levels = useRef<number[]>([]);

  // create once
  useEffect(() => {
    if (!host.current) return;
    const c = createChart(host.current, {
      layout: {
        background: { type: ColorType.Solid, color: C.base },
        textColor: C.muted,
        fontFamily: "'JetBrains Mono', ui-monospace, monospace",
        fontSize: 11,
      },
      grid: {
        vertLines: { color: C.grid },
        horzLines: { color: C.grid },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: C.muted, width: 1, style: LineStyle.Dotted, labelBackgroundColor: C.grid },
        horzLine: { color: C.muted, width: 1, style: LineStyle.Dotted, labelBackgroundColor: C.grid },
      },
      rightPriceScale: { borderVisible: false },
      timeScale: { borderVisible: false, timeVisible: true, secondsVisible: false },
      autoSize: true,
    });

    const s = c.addSeries(CandlestickSeries, {
      upColor: C.bull,
      downColor: C.bear,
      wickUpColor: C.bull,
      wickDownColor: C.bear,
      borderVisible: false,
      // candles alone would autoscale the target line off-screen
      autoscaleInfoProvider: (original: () => AutoscaleInfo | null) => {
        const res = original();
        if (!res?.priceRange || levels.current.length === 0) return res;
        return {
          ...res,
          priceRange: {
            minValue: Math.min(res.priceRange.minValue, ...levels.current),
            maxValue: Math.max(res.priceRange.maxValue, ...levels.current),
          },
        };
      },
    });

    chart.current = c;
    series.current = s;
    return () => {
      c.remove();
      chart.current = null;
      series.current = null;
      lines.current = [];
    };
  }, []);

  // newest time currently in the series, so an out-of-order bar can be
  // dropped rather than thrown back by lightweight-charts
  const lastTime = useRef<number | null>(null);

  // read the freshest bars without making them an effect dependency:
  // a full setData on every tick would fight series.update()
  const barsRef = useRef(bars);
  barsRef.current = bars;

  // full data reset only when the series is genuinely replaced
  useEffect(() => {
    const data = barsRef.current;
    series.current?.setData(data.map(toCandle));
    lastTime.current = data.length ? data[data.length - 1].time : null;
    chart.current?.timeScale().fitContent();
  }, [revision]);

  // decision + structure levels, redrawn whenever the analysis changes
  useEffect(() => {
    const s = series.current;
    if (!s) return;
    lines.current.forEach((l) => s.removePriceLine(l));
    lines.current = [];
    levels.current = [];

    const solid = (price: number, color: string, title: string) =>
      s.createPriceLine({
        price,
        color,
        lineWidth: 1,
        lineStyle: LineStyle.Solid,
        axisLabelVisible: true,
        title,
      });
    const dashed = (price: number, color: string, title: string) =>
      s.createPriceLine({
        price,
        color,
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        title,
      });

    // no_trade returns null prices, so each line is drawn only if real
    const decision: [number | null | undefined, string, string][] = [
      [stage2?.entry, C.muted, "ENTRY"],
      [stage2?.stop, C.bear, "STOP"],
      [stage2?.target, C.bull, "TARGET"],
    ];
    const drawn = decision
      .filter((d): d is [number, string, string] => typeof d[0] === "number")
      .map(([price, color, title]) => solid(price, color, title));

    // stage 1 reports structure as one key_levels array; colour each by
    // which side of the last close it sits on
    const close = barsRef.current[barsRef.current.length - 1]?.close ?? 0;
    const structure = (stage1?.key_levels ?? []).map((price, i) =>
      dashed(price, price <= close ? C.support : C.resist, `L${i + 1}`)
    );

    lines.current = [...drawn, ...structure];
    // lines are always drawn; only a current analysis gets to widen the scale
    levels.current = scaleToLevels
      ? [
          ...decision.map((d) => d[0]).filter((v): v is number => typeof v === "number"),
          ...(stage1?.key_levels ?? []),
        ]
      : [];
    // price lines don't invalidate the price scale on their own
    chart.current?.priceScale("right").applyOptions({ autoScale: true });
  }, [stage1, stage2, scaleToLevels]);

  useImperativeHandle(ref, () => ({
    update: (bar) => {
      // A bar older than the series head is a leftover from the previous
      // subscription, arriving in the gap between switching symbol and the
      // new data landing. Updating with it throws "cannot update oldest
      // data" and would leave the chart wedged.
      if (lastTime.current !== null && bar.time < lastTime.current) return;
      lastTime.current = bar.time;
      series.current?.update(toCandle(bar));
    },
    resetZoom: () => chart.current?.timeScale().fitContent(),
  }));

  return <div ref={host} className="h-full w-full" />;
});

export default Chart;
