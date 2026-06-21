import type { Forecast } from "../types";

// Lightweight dependency-free SVG chart — history line + median projection +
// p10–p90 cone. Matches the QST terminal aesthetic.
const W = 720, H = 250, PAD_L = 46, PAD_R = 12, PAD_T = 14, PAD_B = 20;

function pct(x: number): string {
  return `${x >= 0 ? "+" : ""}${(x * 100).toFixed(0)}%`;
}

export function ForecastChart({ forecast }: { forecast: Forecast }) {
  const hist = forecast.history;
  const proj = forecast.projection;
  const h = hist.length;
  if (h === 0 || proj.length === 0) return null;
  const n = h + proj.length - 1; // projection[0] sits at the history boundary

  const ys: number[] = [];
  hist.forEach((d) => d.close != null && ys.push(d.close));
  proj.forEach((d) => [d.p10, d.p50, d.p90].forEach((v) => v != null && ys.push(v)));
  const yMinRaw = Math.min(...ys), yMaxRaw = Math.max(...ys);
  const padY = (yMaxRaw - yMinRaw) * 0.06 || 1;
  const lo = yMinRaw - padY, hi = yMaxRaw + padY;

  const plotW = W - PAD_L - PAD_R, plotH = H - PAD_T - PAD_B;
  const px = (x: number) => PAD_L + (n <= 1 ? 0 : (x / (n - 1)) * plotW);
  const py = (v: number) => PAD_T + (1 - (v - lo) / (hi - lo)) * plotH;
  const projX = (j: number) => h - 1 + j;

  const histLine = hist
    .map((d, i) => (d.close != null ? `${px(i).toFixed(1)},${py(d.close).toFixed(1)}` : ""))
    .filter(Boolean)
    .join(" ");
  const medLine = proj
    .map((d, j) => (d.p50 != null ? `${px(projX(j)).toFixed(1)},${py(d.p50).toFixed(1)}` : ""))
    .filter(Boolean)
    .join(" ");
  const upper = proj.filter((d) => d.p90 != null).map((d, j) => `${px(projX(j)).toFixed(1)},${py(d.p90!).toFixed(1)}`);
  const lower = proj
    .filter((d) => d.p10 != null)
    .map((d, j) => `${px(projX(j)).toFixed(1)},${py(d.p10!).toFixed(1)}`)
    .reverse();
  const band = [...upper, ...lower].join(" ");
  const boundaryX = px(h - 1);
  const ticks = [hi, (hi + lo) / 2, lo];

  const last = proj[proj.length - 1];
  const upDay = forecast.directional_bias >= 0;

  return (
    <div className="fc">
      <div className="fc__head">
        <h3>
          Predictive Trajectory{" "}
          <span className="fc__sub mono">{forecast.interval} · {forecast.model}</span>
        </h3>
        <span className="fc__stats mono">
          μ {pct(forecast.drift_annual)} · σ {pct(forecast.vol_annual)} · bias{" "}
          <b className={upDay ? "up" : "dn"}>
            {forecast.directional_bias >= 0 ? "+" : ""}{forecast.directional_bias}
          </b>
        </span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="fc__svg">
        {ticks.map((v, i) => (
          <g key={i}>
            <line x1={PAD_L} y1={py(v)} x2={W - PAD_R} y2={py(v)} className="fc__grid" vectorEffect="non-scaling-stroke" />
            <text x={PAD_L - 6} y={py(v) + 3} className="fc__ylabel" textAnchor="end">${v.toFixed(0)}</text>
          </g>
        ))}
        {band && <polygon points={band} className="fc__band" />}
        <line x1={boundaryX} y1={PAD_T} x2={boundaryX} y2={H - PAD_B} className="fc__nowline" vectorEffect="non-scaling-stroke" />
        <text x={boundaryX + 4} y={PAD_T + 9} className="fc__nowlabel">now</text>
        {histLine && <polyline points={histLine} className="fc__hist" vectorEffect="non-scaling-stroke" />}
        {medLine && <polyline points={medLine} className="fc__med" vectorEffect="non-scaling-stroke" />}
      </svg>
      <div className="fc__legend mono">
        <span className="fc__lg fc__lg--hist">history</span>
        <span className="fc__lg fc__lg--med">median p50</span>
        <span className="fc__lg fc__lg--band">p10–p90 cone</span>
        <span className="fc__proj mono">
          {forecast.interval === "1d" ? "horizon" : "intraday"} p50 →
          <b className={upDay ? "up" : "dn"}> ${last.p50?.toFixed(2)}</b>
          {" "}(${last.p10?.toFixed(2)}–${last.p90?.toFixed(2)})
        </span>
      </div>
    </div>
  );
}
