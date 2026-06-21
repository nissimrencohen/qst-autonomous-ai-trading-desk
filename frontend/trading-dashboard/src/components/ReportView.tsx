import { useState } from "react";
import type { ProbabilityReport } from "../types";
import { ForecastChart } from "./ForecastChart";

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

function ExecLevel(props: {
  label: string;
  value: number | null;
  tone?: "bull" | "bear";
  raw?: boolean;
}) {
  const txt =
    props.value == null
      ? "—"
      : props.raw
        ? props.value.toFixed(2)
        : `$${props.value.toFixed(2)}`;
  return (
    <div className={`exec__level ${props.tone ? `exec__level--${props.tone}` : ""}`}>
      <span className="exec__level-label">{props.label}</span>
      <span className="exec__level-value mono">{txt}</span>
    </div>
  );
}

export function ReportView(props: { report: ProbabilityReport | null; blocked: string[] | null }) {
  const [paperFilled, setPaperFilled] = useState(false);
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

        {r.vision && (
          <div className={`card card--vision card--vision-${r.vision.label}`}>
            <h3>Chart Vision</h3>
            <p className="card__big mono" style={{ textTransform: "uppercase" }}>
              {r.vision.label === "bullish" ? "▲" : r.vision.label === "bearish" ? "▼" : "■"}{" "}
              {r.vision.label}
            </p>
            <p className="card__sub mono">
              score {r.vision.score >= 0 ? "+" : ""}{r.vision.score.toFixed(2)} ·
              {" "}conf {(r.vision.confidence * 100).toFixed(0)}%
            </p>
            {Object.entries(r.vision.patterns).filter(([, v]) => v >= 0.4).length > 0 && (
              <div className="chips">
                {Object.entries(r.vision.patterns)
                  .filter(([, v]) => v >= 0.4)
                  .sort((a, b) => b[1] - a[1])
                  .map(([p, v]) => (
                    <span className="chip" key={p}>{p.replace(/_/g, " ")} {(v * 100).toFixed(0)}%</span>
                  ))}
              </div>
            )}
            <p className="card__text">Multimodal LLM read of the uploaded chart (gpt-4o-mini vision).</p>
          </div>
        )}

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

        {r.volatility_view && (
          <div className="card card--vol">
            <h3>Volatility</h3>
            <p className="card__big mono">{r.volatility_view.regime.toUpperCase()}</p>
            <div className="chips">
              <span className="chip">{r.volatility_view.term_structure}</span>
              {r.volatility_view.vix_level != null && (
                <span className="chip">VIX {r.volatility_view.vix_level.toFixed(1)}</span>
              )}
            </div>
            {r.volatility_view.signal && (
              <p className="card__text">{r.volatility_view.signal}</p>
            )}
          </div>
        )}

        {r.space_economy_view && (
          <div className="card card--space">
            <h3>Space Economy</h3>
            <ul className="drivers">
              {r.space_economy_view.key_drivers.length > 0 ? (
                r.space_economy_view.key_drivers.map((d, i) => <li key={i}>{d}</li>)
              ) : (
                <li className="muted">No material space exposure.</li>
              )}
            </ul>
            {r.space_economy_view.launch_cadence && (
              <p className="card__text mono">cadence: {r.space_economy_view.launch_cadence}</p>
            )}
            {r.space_economy_view.rationale && (
              <p className="card__text">{r.space_economy_view.rationale}</p>
            )}
          </div>
        )}
      </div>

      {r.forecast && <ForecastChart forecast={r.forecast} />}

      {r.execution_plan && (
        <div className={`exec exec--${r.execution_plan.side}`}>
          <div className="exec__head">
            <h3>QUANT EXECUTION</h3>
            <span className={`exec__side exec__side--${r.execution_plan.side}`}>
              {r.execution_plan.side.toUpperCase()} · {r.execution_plan.order_type.toUpperCase()}
            </span>
          </div>
          <div className="exec__levels">
            <ExecLevel label="Entry" value={r.execution_plan.entry} />
            <ExecLevel label="Target" value={r.execution_plan.target} tone="bull" />
            <ExecLevel label="Stop-Loss" value={r.execution_plan.stop_loss} tone="bear" />
            <ExecLevel label="R/R" value={r.execution_plan.risk_reward_ratio} raw />
          </div>
          {r.execution_plan.reference_price != null && (
            <p className="exec__ref mono">
              anchored to live ref ${r.execution_plan.reference_price.toFixed(2)}
            </p>
          )}
          {r.execution_plan.rationale && (
            <p className="card__text">{r.execution_plan.rationale}</p>
          )}
          <button
            className="submit exec__btn"
            onClick={() => setPaperFilled(true)}
            disabled={paperFilled || r.execution_plan.entry == null}
          >
            {paperFilled ? "✓ PAPER ORDER SIMULATED" : "EXECUTE TRADE (PAPER)"}
          </button>
          <span className="exec__note mono">
            ⚠ Paper / simulation only — not connected to a live broker, no real capital moves.
          </span>
        </div>
      )}

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
