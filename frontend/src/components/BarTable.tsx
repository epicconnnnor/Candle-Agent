import type { Bar } from "../data";
import { fmtPrice, fmtTime, fmtVolume } from "../data";

const COLS = ["Time", "Open", "High", "Low", "Close", "Volume", "Chg"];

export default function BarTable({ bars }: { bars: Bar[] }) {
  // newest first; each row's change is measured against the bar before it
  const rows = bars
    .map((bar, i) => ({ bar, prev: i > 0 ? bars[i - 1].close : bars[i].open }))
    .reverse();

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[640px] border-collapse">
        <thead>
          <tr className="border-b border-ink">
            {COLS.map((c, i) => (
              <th
                key={c}
                scope="col"
                className={`label pb-3 ${i === 0 ? "text-left" : "text-right"}`}
              >
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map(({ bar, prev }) => {
            const chg = bar.close - prev;
            const up = chg >= 0;
            return (
              <tr key={bar.ts} className="border-b border-rule">
                <td className="num py-3 text-left text-[13px] text-muted">
                  {fmtTime(bar.ts)}
                </td>
                <td className="num py-3 text-right text-[13px]">{fmtPrice(bar.open)}</td>
                <td className="num py-3 text-right text-[13px]">{fmtPrice(bar.high)}</td>
                <td className="num py-3 text-right text-[13px]">{fmtPrice(bar.low)}</td>
                <td className="num py-3 text-right text-[13px] font-medium">
                  {fmtPrice(bar.close)}
                </td>
                <td className="num py-3 text-right text-[13px] text-muted">
                  {fmtVolume(bar.volume)}
                </td>
                <td
                  className={`num py-3 text-right text-[13px] ${
                    up ? "text-navy" : "text-muted"
                  }`}
                >
                  {up ? "+" : "−"}
                  {Math.abs(chg).toFixed(2)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
