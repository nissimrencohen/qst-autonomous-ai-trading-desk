import { useState } from "react";
import { analyze, BlockedError, type AnalyzeParams } from "./api";
import { AgentLog } from "./components/AgentLog";
import { ReportView } from "./components/ReportView";
import { RequestForm } from "./components/RequestForm";
import type { ProbabilityReport, Stage } from "./types";
import { TICKERS } from "./types";

export default function App() {
  const [stage, setStage] = useState<Stage>("idle");
  const [report, setReport] = useState<ProbabilityReport | null>(null);
  const [blocked, setBlocked] = useState<string[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function run(params: AnalyzeParams) {
    setReport(null);
    setBlocked(null);
    setError(null);
    try {
      const rpt = await analyze(params, setStage);
      setReport(rpt);
      setStage("done");
    } catch (err) {
      if (err instanceof BlockedError) {
        setBlocked(err.reasons.length ? err.reasons : ["Request outside desk policy."]);
        setStage("blocked");
      } else {
        setError(String(err));
        setStage("error");
      }
    }
  }

  return (
    <div className="desk">
      <div className="scanlines" aria-hidden="true" />
      <header className="masthead">
        <h1>
          DESK<span className="masthead__slash">/</span>01
        </h1>
        <p className="masthead__sub">AUTONOMOUS AI TRADING DESK — PROBABILITY ENGINE</p>
        <div className="tape" aria-hidden="true">
          <div className="tape__inner">
            {[...TICKERS, ...TICKERS, ...TICKERS].map((t, i) => (
              <span key={i} className="tape__item mono">
                {t} <i className={i % 3 === 1 ? "dn" : "up"}>{i % 3 === 1 ? "▼" : "▲"}</i>
              </span>
            ))}
          </div>
        </div>
      </header>

      {error && (
        <div className="errorbar mono" role="alert">
          ✕ {error}
        </div>
      )}

      <main className="desk__grid">
        <RequestForm stage={stage} onSubmit={run} />
        <ReportView report={report} blocked={blocked} />
        <AgentLog runId={report?.run_id ?? null} />
      </main>

      <footer className="colophon mono">
        rag:8001 · vision:8002 · agents:8003 · rails:8004 — reports are probabilistic
        estimates, never guarantees
      </footer>
    </div>
  );
}
