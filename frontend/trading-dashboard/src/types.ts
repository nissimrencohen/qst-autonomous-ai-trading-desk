/** Mirrors services/agentic-engine/app/schemas.py::ProbabilityReport */

export interface Probabilities {
  bullish: number;
  neutral: number;
  bearish: number;
}

export interface TechnicalView {
  condition_score: number;
  dominant_patterns: string[];
  rationale: string;
}

export interface FundamentalView {
  key_drivers: string[];
  rationale: string;
  sources: string[];
}

export interface RiskAssessment {
  risk_level: "low" | "medium" | "high";
  key_risks: string[];
  max_position_pct: number;
  notes: string;
}

export interface ProbabilityReport {
  run_id: string;
  ticker: string;
  question: string;
  horizon_days: number;
  generated_at: string;
  probabilities: Probabilities;
  technical_view: TechnicalView;
  fundamental_view: FundamentalView;
  risk_assessment: RiskAssessment;
  confidence: number;
  caveats: string[];
  engine_backend: string;
}

export interface RunTraceStep {
  at: string;
  step: string;
  [key: string]: unknown;
}

export interface RunTrace {
  run_id: string;
  ticker: string;
  started_at: string;
  finished_at: string | null;
  steps: RunTraceStep[];
}

export type Stage =
  | "idle"
  | "validating"
  | "retrieving"
  | "synthesizing"
  | "done"
  | "blocked"
  | "error";

export const TICKERS = ["NVDA", "ESLT", "NXSN", "TOND", "CUE"] as const;
