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
  type UTCTimestamp,
} from "lightweight-charts";
import type { Bar, Stage1, Stage2 } from "../types";

const C = {
  base: "#0E1116",
  grid: "#181D25",
  muted: "#6B7686",
  bull: "#26A69A",
  bear: "#EF5350",
  support: "#1E8E7E",
  resist: "#C0392B",
};

/** Imperative handle so a live stream can push bars without a re-render. */
export interface ChartHandle {
  update: (bar: Bar) => void;
  resetZoom: () => void;
}

interface Props {
  bars: Bar[];
  stage1: Stage1;
  stage2: Stage2;
}

const toCandle = (b: Bar) => ({
  time: b.time as UTCTimestamp,
  open: b.open,
  high: b.high,
  low: b.low,
  close: b.close,
});

const Chart = forwardRef<ChartHandle, Props>(function Chart(
  { bars, stage1, stage2 },
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

  // full data reset when the bar set is replaced
  useEffect(() => {
    series.current?.setData(bars.map(toCandle));
    chart.current?.timeScale().fitContent();
  }, [bars]);

  // decision + structure levels, redrawn whenever the analysis changes
  useEffect(() => {
    const s = series.current;
    if (!s) return;
    lines.current.forEach((l) => s.removePriceLine(l));

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

    lines.current = [
      solid(stage2.entry, C.muted, "ENTRY"),
      solid(stage2.stop, C.bear, "STOP"),
      solid(stage2.target, C.bull, "TARGET"),
      ...stage1.support.map((p, i) => dashed(p, C.support, `S${i + 1}`)),
      ...stage1.resistance.map((p, i) => dashed(p, C.resist, `R${i + 1}`)),
    ];

    levels.current = [
      stage2.entry, stage2.stop, stage2.target,
      ...stage1.support, ...stage1.resistance,
    ];
    // price lines don't invalidate the price scale on their own
    chart.current?.priceScale("right").applyOptions({ autoScale: true });
  }, [stage1, stage2]);

  useImperativeHandle(ref, () => ({
    update: (bar) => series.current?.update(toCandle(bar)),
    resetZoom: () => chart.current?.timeScale().fitContent(),
  }));

  return <div ref={host} className="h-full w-full" />;
});

export default Chart;
