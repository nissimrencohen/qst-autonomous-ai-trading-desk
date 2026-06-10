import type { ProbabilityReport } from "../types";

function Bar(props: { label: string; value: number; tone: "bull" | "flat" | "bear" }) {
  return (
    <div className={`prob prob--${props.tone}`}>
      <div className="prob__meta">
        <span className="prob__label">{props.label}</span>
        <span className="prob__value mono">{(props.value * 100).toFixed(1)}%</span>
      </div>
      <div className="prob__track">
        <div className="prob__fill" style={{ width: `${props.value * 100}%` }} />
      </div>
    </div>
  );
}

export function ReportView(props: { report: ProbabilityReport | null; blocked: string[] | null }) {
  const { report, blocked } = props;

  if (blocked) {
    return (
      <section className="panel panel--report" style={{ animationDelay: "0.12s" }}>
        <header className="panel__head">
          <h2>Probability Report</h2>
          <span className="panel__tag panel__tag--alert">BLOCKED</span>
        </header>
        <div className="blocked">
          <p className="blocked__title">Request rejected by the input rail.</p>
          <ul>
            {blocked.map((r, i) => (
              <li key={i}>{r}</li>
            ))}
          </ul>
        </div>
      </section>
    );
  }

  if (!report) {
    return (
      <section className="panel panel--report panel--empty" style={{ animationDelay: "0.12s" }}>
        <header className="panel__head">
          <h2>Probability Report</h2>
          <span className="panel__tag">RPT/—</span>
        </header>
        <div className="empty">
          <div className="empty__glyph">◬</div>
          <p>Submit an order ticket to generate a desk report.</p>
        </div>
      </section>
    );
  }

  const r = report;
  return (
    <section className="panel panel--report" style={{ animationDelay: "0.12s" }}>
      <header className="panel__head">
        <h2>
          {r.ticker} <span className="panel__sub mono">{r.horizon_days}d horizon</span>
        </h2>
        <span className="panel__tag">RPT/{r.run_id.slice(0, 6).toUpperCase()}</span>
      </header>

      <p className="report__question">“{r.question}”</p>

      <div className="probs">
        <Bar label="Bullish" value={r.probabilities.bullish} tone="bull" />
        <Bar label="Neutral" value={r.probabilities.neutral} tone="flat" />
        <Bar label="Bearish" value={r.probabilities.bearish} tone="bear" />
      </div>

      <div className="report__grid">
        <div className="card">
          <h3>Technical</h3>
          <p className="card__big mono">
            {r.technical_view.condition_score >= 0 ? "+" : ""}
            {r.technical_view.condition_score.toFixed(2)}
          </p>
          {r.technical_view.dominant_patterns.length > 0 && (
            <div className="chips">
              {r.technical_view.dominant_patterns.map((p) => (
                <span className="chip" key={p}>
                  {p.replace(/_/g, " ")}
                </span>
              ))}
            </div>
          )}
          <p className="card__text">{r.technical_view.rationale}</p>
        </div>

        <div className="card">
          <h3>Fundamental</h3>
          <ul className="drivers">
            {r.fundamental_view.key_drivers.length > 0 ? (
              r.fundamental_view.key_drivers.map((d, i) => <li key={i}>{d}</li>)
            ) : (
              <li className="muted">No covered drivers.</li>
            )}
          </ul>
          {r.fundamental_view.sources.length > 0 && (
            <p className="card__sources mono">src: {r.fundamental_view.sources.join(" · ")}</p>
          )}
        </div>

        <div className={`card card--risk card--risk-${r.risk_assessment.risk_level}`}>
          <h3>Risk</h3>
          <p className="card__big">{r.risk_assessment.risk_level.toUpperCase()}</p>
          <p className="card__text mono">max position {r.risk_assessment.max_position_pct}%</p>
          <ul className="risks">
            {r.risk_assessment.key_risks.map((k, i) => (
              <li key={i}>{k}</li>
            ))}
          </ul>
        </div>
      </div>

      <footer className="report__foot">
        <span className="mono">
          confidence {(r.confidence * 100).toFixed(0)}% · engine {r.engine_backend} ·{" "}
          {r.generated_at}
        </span>
        <ul className="caveats">
          {r.caveats.map((c, i) => (
            <li key={i}>※ {c}</li>
          ))}
        </ul>
      </footer>
    </section>
  );
}
