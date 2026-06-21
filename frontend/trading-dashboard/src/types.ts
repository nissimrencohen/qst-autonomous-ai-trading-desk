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

export interface ExecutionPlan {
  side: "long" | "short" | "flat";
  order_type: "market" | "limit" | "stop";
  entry: number | null;
  target: number | null;
  stop_loss: number | null;
  risk_reward_ratio: number | null;
  reference_price: number | null;
  rationale: string;
  paper_only: boolean;
}

export interface VolatilityView {
  vix_level: number | null;
  term_structure: "contango" | "backwardation" | "flat" | "unknown";
  front_month: number | null;
  back_month: number | null;
  regime: "calm" | "elevated" | "stress" | "panic" | "unknown";
  signal: string;
}

export interface SpaceEconomyView {
  key_drivers: string[];
  launch_cadence: string;
  rationale: string;
  sources: string[];
}

export interface ForecastPoint {
  t: string;
  close?: number | null;
  p10?: number | null;
  p50?: number | null;
  p90?: number | null;
}

export interface Forecast {
  ticker: string;
  interval: string;
  model: string;
  anchor_price: number;
  drift_annual: number;
  vol_annual: number;
  directional_bias: number;
  history: ForecastPoint[];
  projection: ForecastPoint[];
  generated_at: string;
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
  execution_plan?: ExecutionPlan | null;
  volatility_view?: VolatilityView | null;
  space_economy_view?: SpaceEconomyView | null;
  forecast?: Forecast | null;
  vision?: VisionAnalysis | null;
}

export interface VisionAnalysis {
  score: number;            // -1..1 (bearish..bullish)
  label: "bullish" | "bearish" | "neutral";
  confidence: number;       // 0..1
  patterns: Record<string, number>;
}

export interface RunTraceStep {
  at: string;
  step: string;
  [key: string]: unknown;
}

export type RunStatus = "running" | "done" | "blocked" | "error";

export interface RunTrace {
  run_id: string;
  ticker: string;
  started_at: string;
  finished_at: string | null;
  steps: RunTraceStep[];
  // async run/poll lifecycle (Phase 1)
  status?: RunStatus;
  report?: ProbabilityReport | null;
  error?: string | null;
  blocked_reasons?: string[];
}

export type Stage =
  | "idle"
  | "validating"
  | "retrieving"
  | "synthesizing"
  | "done"
  | "blocked"
  | "error";

// V2.0 watchlist — the desk's exact 10 approved instruments.
export const TICKERS = [
  "SPCX", "MSFT", "AAPL", "NVDA", "GOOGL",
  "AMZN", "UPRO", "TQQQ", "VIXY", "SVXY",
] as const;

export const VOL_TICKERS = ["VIXY", "SVXY"] as const;

export function isVolTicker(t: string): boolean {
  return (VOL_TICKERS as readonly string[]).includes(t);
}

// ── Regime alert log ─────────────────────────────────────────────────────────

export interface AlertEntry {
  id: string;
  at: string;
  kind: "regime" | "heat";
  prev: string;
  curr: string;
}

// ── Market Live types ─────────────────────────────────────────────────────────

export interface PositionSize {
  shares: number;
  notional: number;
  pct: number;
}

export interface TickerSignal {
  name: string;
  price: number;
  change_pct: number;
  signal: "bullish" | "bearish" | "neutral";
  strength: number;
  ma20: number;
  ma50: number;
  atr: number;
  entry_zone: [number, number];
  target: number;
  stop: number;
  risk_reward: number;
  position_sizes: Record<string, PositionSize>;
  "52w_high": number | null;
  "52w_low": number | null;
}

export interface VixData {
  price: number;
  vix_9d: number;
  vix_30d: number;
  vix_3m: number;
  term_structure: "contango" | "backwardation" | "flat";
  regime: "calm" | "elevated" | "stress" | "panic";
  spread: number;
  uvxy_signal: string;
  regime_advice: string;
}

// ── Daily Briefing types (v1.4) ───────────────────────────────────────────────

export interface MoveProbs {
  p_up_1pct: number;   p_down_1pct: number;   p_move_1pct: number;
  p_up_2pct: number;   p_down_2pct: number;   p_move_2pct: number;
  p_up_3pct: number;   p_down_3pct: number;   p_move_3pct: number;
  p_up_5pct: number;   p_down_5pct: number;   p_move_5pct: number;
  p_up_10pct: number;  p_down_10pct: number;  p_move_10pct: number;
  vol_daily_pct: number;
  expected_daily_range_pct: number;
  vol_annual?: number;
  drift_annual?: number;
  _source?: string;
}

export interface InstrumentCrewSignal {
  bullish: number | null;
  neutral: number | null;
  bearish: number | null;
  confidence: number | null;
  risk_level: "low" | "medium" | "high" | null;
  max_position_pct: number | null;
  execution_side: "long" | "short" | "flat";
  entry: number | null;
  stop_loss: number | null;
  target: number | null;
  risk_reward: number | null;
}

export interface InstrumentBriefing {
  ticker: string;
  run_id: string;
  status: "done" | "running" | "blocked" | "error" | "timeout";
  overnight_gap_pct: number | null;
  intraday_30m: {
    open: number; high: number; low: number; close: number;
    volume: number; range_pct: number; momentum_pct: number;
  } | null;
  crew: InstrumentCrewSignal;
  move_probs_1d: MoveProbs;
  move_probs_5d: MoveProbs;
}

export interface DailyBriefing {
  briefing_date: string;
  generated_at: string;
  market_context: {
    vix: number;
    regime: "calm" | "elevated" | "stress" | "panic";
    vix_implied_1d: MoveProbs;
    vix_implied_5d: MoveProbs;
  };
  instruments: InstrumentBriefing[];
  engine_backend: string;
  status: "complete" | "partial";
}

export interface MarketLive {
  timestamp: string;
  vix: VixData;
  indices: Record<string, { price: number; change_pct: number }>;
  tickers: Record<string, TickerSignal>;
  risk_summary: {
    market_heat: "low" | "medium" | "high" | "extreme";
    recommended_exposure_pct: number;
    hedging_advice: string;
  };
}

// ── Continuous Synthesis Loop (Step 2e/2g) ────────────────────────────────────

/** Structured macro+fear block persisted with each continuous report. */
export interface SynthesisMacro {
  macro?: {
    sp500?: { symbol?: string; price: number; change_pct: number } | null;
    nasdaq?: { symbol?: string; price: number; change_pct: number } | null;
    market_tone?: string;
    error?: string;
  };
  vix?: {
    vix_30d?: number;
    term_structure?: "contango" | "backwardation" | "flat";
    regime?: "calm" | "elevated" | "stress" | "panic";
    error?: string;
  };
}

export interface SynthesisReport {
  ticker: string;
  run_id: string;
  report: ProbabilityReport;
  macro: SynthesisMacro | null;
  generated_at: string;
  updated_at: string;
}

export interface SynthesisLatest {
  count: number;
  reports: SynthesisReport[];
}

export interface SynthesisStatus {
  enabled: boolean;
  cursor?: number;
  last_ticker?: string | null;
  last_status?: string | null;
  heartbeat?: string | null;
  reports_count?: number;
  message?: string;
}

// ── Ingestion Engine status (Step 2d) ─────────────────────────────────────────

export interface IngestionTickerStat {
  ticker: string;
  rows: number;
  latest: string | null;
}

export interface IngestionStatus {
  enabled: boolean;
  interval_s?: number;
  db_path?: string;
  total: number;
  by_source_type: Record<string, number>;
  by_ticker: IngestionTickerStat[];
  latest_ingested_at: string | null;
  error?: string;
}

// ── V2.0 Chat Assistant types ─────────────────────────────────────────────────

export interface ChatMessage {
  role: "user" | "assistant" | "system";
  content: string;
  created_at?: string;
  model_used?: string;
}

export interface ChatSession {
  session_id: string;
  created_at: string;
  title?: string;
}
