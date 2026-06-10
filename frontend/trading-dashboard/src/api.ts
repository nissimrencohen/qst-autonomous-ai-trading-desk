import type { ProbabilityReport, RunTrace, Stage } from "./types";

/**
 * Primary path: the n8n orchestrator webhook (payload validation, guardrails,
 * parallel fan-out, synthesis, output rail) — the production data flow.
 * Fallback path: drive the four services directly from the browser so the
 * desk stays usable in dev or when n8n is down (degraded mode, no rails
 * skipped — guardrails are still called explicitly).
 */
const env = import.meta.env;
const ORCHESTRATOR_URL: string =
  env.VITE_ORCHESTRATOR_URL ?? "/webhook/analyze";
const RAG_URL: string = env.VITE_RAG_URL ?? "http://localhost:8001";
const VISION_URL: string = env.VITE_VISION_URL ?? "http://localhost:8002";
const AGENTIC_URL: string = env.VITE_AGENTIC_URL ?? "http://localhost:8003";
const GUARDRAILS_URL: string = env.VITE_GUARDRAILS_URL ?? "http://localhost:8004";

export interface AnalyzeParams {
  ticker: string;
  question: string;
  horizonDays: number;
  chart: File | null;
}

export class BlockedError extends Error {
  reasons: string[];
  constructor(reasons: string[]) {
    super("request blocked by guardrails");
    this.reasons = reasons;
  }
}

async function postJson<T>(url: string, body: unknown, timeoutMs = 60000): Promise<T> {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(timeoutMs),
  });
  if (!res.ok) throw new Error(`${url} -> ${res.status}`);
  return res.json() as Promise<T>;
}

function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve((reader.result as string).split(",")[1] ?? "");
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

async function viaOrchestrator(p: AnalyzeParams): Promise<ProbabilityReport> {
  const payload = {
    ticker: p.ticker,
    question: p.question,
    horizon_days: p.horizonDays,
    chart_base64: p.chart ? await fileToBase64(p.chart) : null,
    chart_content_type: p.chart?.type ?? null,
  };
  const body = await postJson<Record<string, unknown>>(ORCHESTRATOR_URL, payload, 120000);
  if (body["blocked"]) throw new BlockedError((body["reasons"] as string[]) ?? []);
  return body as unknown as ProbabilityReport;
}

async function viaServices(
  p: AnalyzeParams,
  onStage: (s: Stage) => void,
): Promise<ProbabilityReport> {
  onStage("validating");
  const gate = await postJson<{ allowed: boolean; violations: { detail: string }[] }>(
    `${GUARDRAILS_URL}/validate/input`,
    { question: p.question, ticker: p.ticker, source: "dashboard-direct" },
  );
  if (!gate.allowed) throw new BlockedError(gate.violations.map((v) => v.detail));

  onStage("retrieving");
  const ragPromise = postJson<{
    summary: string | null;
    retrieved: unknown[];
  }>(`${RAG_URL}/query`, { ticker: p.ticker, question: p.question, k: 4 });

  let visionPromise: Promise<unknown | null> = Promise.resolve(null);
  if (p.chart) {
    const form = new FormData();
    form.append("ticker", p.ticker);
    form.append("chart", p.chart);
    visionPromise = fetch(`${VISION_URL}/analyse`, { method: "POST", body: form }).then(
      (r) => (r.ok ? r.json() : null),
    );
  }
  const [rag, vision] = await Promise.all([ragPromise, visionPromise]);

  onStage("synthesizing");
  return postJson<ProbabilityReport>(`${AGENTIC_URL}/synthesize`, {
    ticker: p.ticker,
    question: p.question,
    horizon_days: p.horizonDays,
    rag,
    vision,
  });
}

export async function analyze(
  p: AnalyzeParams,
  onStage: (s: Stage) => void,
): Promise<ProbabilityReport> {
  try {
    onStage("validating");
    return await viaOrchestrator(p);
  } catch (err) {
    if (err instanceof BlockedError) throw err;
    // orchestrator unreachable -> degraded direct mode
    return viaServices(p, onStage);
  }
}

export async function fetchTrace(runId: string): Promise<RunTrace | null> {
  const res = await fetch(`${AGENTIC_URL}/runs/${runId}`);
  return res.ok ? ((await res.json()) as RunTrace) : null;
}
