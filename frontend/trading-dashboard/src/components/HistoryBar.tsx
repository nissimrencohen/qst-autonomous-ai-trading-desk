import type { ProbabilityReport } from "../types";

/** Horizontal strip of past analyses; click to re-open one (persists across refresh). */
export function HistoryBar(props: {
  items: ProbabilityReport[];
  currentRunId: string | null;
  onSelect: (r: ProbabilityReport) => void;
}) {
  if (props.items.length === 0) return null;
  return (
    <div className="history">
      <span className="history__label mono">HISTORY</span>
      <div className="history__rail">
        {props.items.map((r) => {
          const bull = Math.round(r.probabilities.bullish * 100);
          const tone =
            r.probabilities.bullish > r.probabilities.bearish ? "up" : "dn";
          const active = r.run_id === props.currentRunId;
          const t = r.generated_at.slice(11, 16);
          return (
            <button
              key={r.run_id}
              className={`history__chip ${active ? "history__chip--on" : ""}`}
              onClick={() => props.onSelect(r)}
              title={`${r.question} — ${r.generated_at}`}
            >
              <span className="history__sym">{r.ticker}</span>
              <span className={`history__bull mono ${tone}`}>{bull}%</span>
              <span className="history__time mono">{t}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
