import { useEffect, useRef, useState } from "react";
import type { Stage } from "../types";
import { TICKERS, isVolTicker } from "../types";
import type { AnalyzeParams } from "../api";

const STAGES: { key: Stage; label: string }[] = [
  { key: "validating", label: "GUARDRAILS / INPUT RAIL" },
  { key: "retrieving", label: "RAG + VISION FAN-OUT" },
  { key: "synthesizing", label: "AGENT CREW SYNTHESIS" },
  { key: "done", label: "REPORT VALIDATED" },
];

export function RequestForm(props: {
  stage: Stage;
  onSubmit: (p: AnalyzeParams) => void;
  initialTicker?: string | null;
}) {
  const [ticker, setTicker] = useState<string>(TICKERS[0]);
  // pre-select the ticker the user clicked on a Command Center desk
  useEffect(() => {
    if (props.initialTicker) setTicker(props.initialTicker);
  }, [props.initialTicker]);
  const [question, setQuestion] = useState(
    "What is the probability of upside into the next monthly expiry?",
  );
  const [horizon, setHorizon] = useState(30);
  const [chart, setChart] = useState<File | null>(null);
  const [volDesk, setVolDesk] = useState(false);
  const [intraday, setIntraday] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const effectiveHorizon = intraday ? 1 : horizon;

  const busy =
    props.stage === "validating" ||
    props.stage === "retrieving" ||
    props.stage === "synthesizing";
  const activeIdx = STAGES.findIndex((s) => s.key === props.stage);

  // Volatility instruments force the desk on; otherwise it's a manual toggle.
  const volForced = isVolTicker(ticker);
  const effectiveVolDesk = volForced || volDesk;

  return (
    <section className="panel panel--form" style={{ animationDelay: "0.05s" }}>
      <header className="panel__head">
        <h2>Order Ticket</h2>
        <span className="panel__tag">REQ/01</span>
      </header>

      <label className="field">
        <span className="field__label">Instrument</span>
        <div className="ticker-grid">
          {TICKERS.map((t) => (
            <button
              key={t}
              type="button"
              className={`ticker-chip ${t === ticker ? "ticker-chip--on" : ""}`}
              onClick={() => setTicker(t)}
              disabled={busy}
            >
              {t}
            </button>
          ))}
        </div>
      </label>

      <label className="field">
        <span className="field__label">Analyst question</span>
        <textarea
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          rows={4}
          maxLength={500}
          disabled={busy}
        />
      </label>

      <label className="field">
        <span className="field__label">Prediction interval</span>
        <div className="ticker-grid">
          <button
            type="button"
            className={`ticker-chip ${!intraday ? "ticker-chip--on" : ""}`}
            onClick={() => setIntraday(false)}
            disabled={busy}
          >
            MULTI-DAY
          </button>
          <button
            type="button"
            className={`ticker-chip ${intraday ? "ticker-chip--on" : ""}`}
            onClick={() => setIntraday(true)}
            disabled={busy}
          >
            INTRADAY 5m
          </button>
        </div>
        {intraday && (
          <span className="field__hint mono">5-minute bars · 1-day projection · overrides horizon</span>
        )}
      </label>

      <label className="field">
        <span className="field__label">
          Horizon — <b className="mono">{intraday ? "1d (intraday)" : `${horizon}d`}</b>
        </span>
        <input
          type="range"
          min={7}
          max={180}
          step={1}
          value={horizon}
          onChange={(e) => setHorizon(Number(e.target.value))}
          disabled={busy || intraday}
        />
      </label>

      <label className="field">
        <span className="field__label">Desk mode</span>
        <button
          type="button"
          className={`ticker-chip ${effectiveVolDesk ? "ticker-chip--on" : ""}`}
          onClick={() => !volForced && setVolDesk((v) => !v)}
          disabled={busy || volForced}
          title={
            volForced
              ? "Auto-armed for volatility instruments"
              : "Toggle VIX term-structure analysis"
          }
        >
          {effectiveVolDesk ? "◉ VOLATILITY DESK" : "○ VOLATILITY DESK"}
        </button>
        {effectiveVolDesk && (
          <span className="field__hint mono">
            VIX term structure · contango/backwardation · regime
          </span>
        )}
      </label>

      <label className="field">
        <span className="field__label">Chart screenshot (optional)</span>
        <input
          ref={fileRef}
          type="file"
          accept="image/png,image/jpeg,image/webp"
          onChange={(e) => setChart(e.target.files?.[0] ?? null)}
          disabled={busy}
        />
        {chart && <span className="field__hint mono">{chart.name}</span>}
      </label>

      <button
        className="submit"
        disabled={busy || question.trim().length < 3}
        onClick={() =>
          props.onSubmit({
            ticker,
            question: question.trim(),
            horizonDays: effectiveHorizon,
            chart,
            volatilityDesk: effectiveVolDesk,
            interval: intraday ? "5m" : "1d",
          })
        }
      >
        {busy ? "WORKING…" : "RUN ANALYSIS"}
      </button>

      <ol className="pipeline">
        {STAGES.map((s, i) => (
          <li
            key={s.key}
            className={
              "pipeline__step" +
              (i < activeIdx || props.stage === "done" ? " pipeline__step--past" : "") +
              (i === activeIdx && props.stage !== "done" ? " pipeline__step--now" : "")
            }
          >
            {s.label}
          </li>
        ))}
      </ol>
    </section>
  );
}
