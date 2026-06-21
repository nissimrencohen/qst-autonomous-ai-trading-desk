import type {
  ProbabilityReport, RunTrace, Stage, MarketLive, DailyBriefing,
  SynthesisLatest, SynthesisStatus, IngestionStatus,
  ChatMessage, ChatSession,
} from "./types";

const env = import.meta.env;
const RAG_URL: string    = env.VITE_RAG_URL    ?? "http://localhost:8001";
const AGENTIC_URL: string = env.VITE_AGENTIC_URL ?? "http://localhost:8003";
// Primary analysis entrypoint. n8n's /webhook/analyze now dispatches to the
// agentic async /analyze and returns a run_id immediately (guardrails, RAG and
// vision orchestration all happen server-side inside the agentic engine).
const ORCHESTRATOR_URL: string = env.VITE_ORCHESTRATOR_URL ?? "/webhook/analyze";

export interface AnalyzeParams {
  ticker: string;
  question: string;
  horizonDays: number;
  chart: File | null;
  volatilityDesk?: boolean;
  interval?: "5m" | "1d";
}

// ── Auth / RBAC ───────────────────────────────────────────────────────────────

export type Role = "admin" | "user";

export interface LoginResult {
  access_token: string;
  token_type: string;
  role: Role;
  username: string;
}

const TOKEN_KEY = "desk.auth.token";

/** Bearer token persisted across reloads; read by authed requests. */
export function getAuthToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setAuthToken(token: string | null): void {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* private mode / disabled storage — token stays in memory only */
  }
}

export function authHeaders(): Record<string, string> {
  const t = getAuthToken();
  return t ? { Authorization: `Bearer ${t}` } : {};
}

/**
 * DB-backed login. POSTs OAuth2 form-encoded credentials to /auth/token and
 * returns the issued JWT plus the user's role.
 */
export async function login(username: string, password: string): Promise<LoginResult> {
  const body = new URLSearchParams();
  body.set("username", username);
  body.set("password", password);
  const res = await fetch(`${AGENTIC_URL}/auth/token`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: body.toString(),
    signal: AbortSignal.timeout(15000),
  });
  if (!res.ok) {
    let detail = `Login failed (${res.status})`;
    try {
      const data = await res.json();
      if (typeof data?.detail === "string") detail = data.detail;
    } catch {
      /* keep generic message */
    }
    throw new Error(detail);
  }
  return (await res.json()) as LoginResult;
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

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

/** Map the latest run-trace steps to the dashboard's coarse pipeline stage. */
function stageFromTrace(t: RunTrace): Stage {
  const steps = t.steps.map((s) => String(s.step));
  const synth = ["crew_kickoff", "agent_output", "memory_load", "risk_synthesis", "technical_analysis"];
  if (steps.some((s) => synth.includes(s))) return "synthesizing";
  if (steps.includes("rag_query")) return "retrieving";
  return "validating";
}

/** Kick off an analysis and return its run_id (no blocking on the crew). */
async function startAnalyze(p: AnalyzeParams): Promise<string> {
  const payload = {
    ticker: p.ticker,
    question: p.question,
    horizon_days: p.horizonDays,
    volatility_desk: p.volatilityDesk ?? false,
    chart_base64: p.chart ? await fileToBase64(p.chart) : null,
    chart_content_type: p.chart?.type ?? null,
    interval: p.interval ?? "1d",
  };
  // Primary: n8n orchestrator returns { run_id } fast. Short timeout so a slow or
  // legacy n8n falls back to the agentic /analyze endpoint directly (same shape).
  try {
    const body = await postJson<{ run_id?: string; blocked?: boolean; reasons?: string[] }>(
      ORCHESTRATOR_URL,
      payload,
      15000,
    );
    if (body.blocked) throw new BlockedError(body.reasons ?? []);
    if (body.run_id) return body.run_id;
    throw new Error("orchestrator returned no run_id");
  } catch (err) {
    if (err instanceof BlockedError) throw err;
    const body = await postJson<{ run_id: string }>(`${AGENTIC_URL}/analyze`, payload, 15000);
    return body.run_id;
  }
}

/** Poll GET /runs/{id} until the background job resolves to a report. */
async function pollRun(
  runId: string,
  onStage: (s: Stage) => void,
  timeoutMs = 300000,
): Promise<ProbabilityReport> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const res = await fetch(`${AGENTIC_URL}/runs/${runId}`).catch(() => null);
    if (res && res.ok) {
      const trace = (await res.json()) as RunTrace;
      onStage(stageFromTrace(trace));
      if (trace.status === "done" && trace.report) return trace.report;
      if (trace.status === "blocked") throw new BlockedError(trace.blocked_reasons ?? []);
      if (trace.status === "error") throw new Error(trace.error ?? "analysis failed");
    }
    await sleep(1500);
  }
  throw new Error("analysis timed out after 5 minutes");
}

export async function analyze(
  p: AnalyzeParams,
  onStage: (s: Stage) => void,
  onRunId?: (runId: string) => void,
): Promise<ProbabilityReport> {
  onStage("validating");
  const runId = await startAnalyze(p);
  onRunId?.(runId); // surface run_id so the live Agent Trace can stream immediately
  return pollRun(runId, onStage);
}

export async function fetchTrace(runId: string): Promise<RunTrace | null> {
  const res = await fetch(`${AGENTIC_URL}/runs/${runId}`);
  return res.ok ? ((await res.json()) as RunTrace) : null;
}

export async function fetchMarketLive(): Promise<MarketLive> {
  const res = await fetch(`${RAG_URL}/market-live`, {
    signal: AbortSignal.timeout(60000),
  });
  if (!res.ok) throw new Error(`market-live -> ${res.status}`);
  return res.json() as Promise<MarketLive>;
}

// ── Daily Briefing API (v1.4) ─────────────────────────────────────────────────

export async function fetchDailyBriefing(): Promise<DailyBriefing | null> {
  const res = await fetch(`${AGENTIC_URL}/daily-briefing/latest`, {
    signal: AbortSignal.timeout(10000),
  });
  if (!res.ok) return null;
  const data = await res.json();
  if (data?.status === "not_available") return null;
  return data as DailyBriefing;
}

export async function triggerDailyBriefing(): Promise<{ status: string; message: string }> {
  const res = await fetch(`${AGENTIC_URL}/daily-briefing/trigger`, {
    method: "POST",
    signal: AbortSignal.timeout(10000),
  });
  if (!res.ok) throw new Error(`trigger failed: ${res.status}`);
  return res.json();
}

// ── Continuous Synthesis Loop API (Step 2e/2g) ────────────────────────────────

export async function fetchSynthesisLatest(): Promise<SynthesisLatest> {
  const res = await fetch(`${AGENTIC_URL}/synthesis/latest`, {
    signal: AbortSignal.timeout(15000),
  });
  if (!res.ok) throw new Error(`synthesis/latest -> ${res.status}`);
  return res.json() as Promise<SynthesisLatest>;
}

export async function fetchSynthesisStatus(): Promise<SynthesisStatus | null> {
  const res = await fetch(`${AGENTIC_URL}/synthesis/status`, {
    signal: AbortSignal.timeout(10000),
  }).catch(() => null);
  if (!res || !res.ok) return null;
  return res.json() as Promise<SynthesisStatus>;
}

export async function fetchIngestionStatus(): Promise<IngestionStatus | null> {
  const res = await fetch(`${AGENTIC_URL}/ingestion/status`, {
    signal: AbortSignal.timeout(10000),
  }).catch(() => null);
  if (!res || !res.ok) return null;
  return res.json() as Promise<IngestionStatus>;
}

export async function fetchMoveProbsOnDemand(
  ticker: string,
  horizonDays: number = 1,
): Promise<{ ticker: string; horizon_trading_days: number; move_probs: Record<string, number> } | null> {
  const res = await fetch(
    `${AGENTIC_URL}/daily-briefing/move-probs/${ticker}?horizon_days=${horizonDays}`,
    { signal: AbortSignal.timeout(15000) },
  );
  if (!res.ok) return null;
  return res.json();
}

// ── V2.0 Chat Assistant API ───────────────────────────────────────────────────

export interface ChatSendOptions {
  messages: ChatMessage[];
  sessionId?: string | null;
  tickerContext?: string | null;
  onDelta: (chunk: string) => void;
  onDone: (sessionId: string, modelUsed: string, finalContent: string) => void;
  onError: (msg: string) => void;
}

/**
 * Send a chat message and stream the response via SSE.
 * Calls POST /chat on the agentic-engine and emits delta events in real-time.
 */
export async function sendChatMessage(opts: ChatSendOptions): Promise<void> {
  const res = await fetch(`${AGENTIC_URL}/chat/`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      messages: opts.messages,
      session_id: opts.sessionId ?? null,
      ticker_context: opts.tickerContext ?? null,
    }),
    signal: AbortSignal.timeout(60000),
  });

  if (!res.ok || !res.body) {
    let errMsg = `Chat API error: ${res.status}`;
    try {
      const errData = await res.json();
      if (errData?.detail?.message) errMsg = errData.detail.message;
      else if (typeof errData?.detail === 'string') errMsg = errData.detail;
    } catch {}
    opts.onError(errMsg);
    return;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let finalSessionId = opts.sessionId ?? "";
  let finalModel = "";
  let finalContent = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";
    for (const line of lines) {
      if (!line.startsWith("data: ")) continue;
      try {
        const data = JSON.parse(line.slice(6));
        if (data.type === "delta") {
          opts.onDelta(data.content as string);
        } else if (data.type === "done") {
          finalModel = data.model_used as string;
          if (data.accumulated) finalContent = data.accumulated as string;
        } else if (data.type === "session_id") {
          finalSessionId = data.session_id as string;
        } else if (data.type === "error") {
          opts.onError(data.content as string);
        }
      } catch {
        // non-JSON line, skip
      }
    }
  }

  opts.onDone(finalSessionId, finalModel, finalContent);
}

export async function loadChatHistory(
  sessionId: string,
): Promise<ChatMessage[]> {
  const res = await fetch(`${AGENTIC_URL}/chat/history/${sessionId}`, {
    signal: AbortSignal.timeout(10000),
  });
  if (!res.ok) return [];
  const data = await res.json();
  return (data.messages ?? []) as ChatMessage[];
}

export async function listChatSessions(): Promise<ChatSession[]> {
  const res = await fetch(`${AGENTIC_URL}/chat/sessions`, {
    signal: AbortSignal.timeout(10000),
  }).catch(() => null);
  if (!res || !res.ok) return [];
  const data = await res.json();
  return (data.sessions ?? []) as ChatSession[];
}

// ── V2.0 Admin / Manual Upload API ───────────────────────────────────────────

export interface IngestDocument {
  ticker: string;
  source: string;
  title: string;
  text: string;
  published_at: string;
}

export async function ingestDocument(
  doc: IngestDocument,
): Promise<{ success: boolean; total_documents?: number; error?: string }> {
  try {
    const res = await fetch(`${RAG_URL}/ingest`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ documents: [doc] }),
      signal: AbortSignal.timeout(60000),
    });
    const body = await res.json();
    if (res.ok) return { success: true, ...body };
    return { success: false, error: body.detail ?? String(res.status) };
  } catch (e) {
    return { success: false, error: String(e) };
  }
}

export async function ingestBulkJson(
  documents: IngestDocument[],
): Promise<{ success: boolean; total_documents?: number; error?: string }> {
  try {
    const res = await fetch(`${RAG_URL}/ingest`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ documents }),
      signal: AbortSignal.timeout(120000),
    });
    const body = await res.json();
    if (res.ok) return { success: true, ...body };
    return { success: false, error: body.detail ?? String(res.status) };
  } catch (e) {
    return { success: false, error: String(e) };
  }
}

export async function ingestOmnibus(
  formData: FormData,
): Promise<{ success: boolean; total_documents?: number; error?: string }> {
  try {
    const res = await fetch(`${RAG_URL}/ingest-omnibus`, {
      method: "POST",
      body: formData,
      signal: AbortSignal.timeout(120000),
    });
    const body = await res.json();
    if (res.ok) return { success: true, ...body };
    return { success: false, error: body.detail ?? String(res.status) };
  } catch (e) {
    return { success: false, error: String(e) };
  }
}

export interface VisionResult {
  label: "bullish" | "bearish" | "neutral";
  score: number;
  confidence: number;
  patterns: Record<string, unknown>;
  model_backend: string;
}

export async function analyseChart(
  ticker: string,
  imageFile: File,
): Promise<{ success: true; result: VisionResult } | { success: false; error: string }> {
  const VISION_URL: string = env.VITE_VISION_URL ?? "http://localhost:8002";
  try {
    const form = new FormData();
    form.append("ticker", ticker);
    form.append("chart", imageFile, imageFile.name);
    const res = await fetch(`${VISION_URL}/analyse`, {
      method: "POST",
      body: form,
      signal: AbortSignal.timeout(60000),
    });
    if (res.ok) return { success: true, result: await res.json() };
    const txt = await res.text();
    return { success: false, error: `${res.status}: ${txt}` };
  } catch (e) {
    return { success: false, error: String(e) };
  }
}
