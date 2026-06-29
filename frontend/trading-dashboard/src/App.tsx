import { useEffect, useRef, useState, useCallback } from "react";
import { fetchMarketLive, analyze, BlockedError, type AnalyzeParams } from "./api";
import { ReportView } from "./components/ReportView";
import { RequestForm } from "./components/RequestForm";
import { AgentLog } from "./components/AgentLog";
import { CommandCenter } from "./components/CommandCenter";
import { HistoryBar } from "./components/HistoryBar";
import { AlertToast } from "./components/AlertToast";
import { AlertLog } from "./components/AlertLog";
import { DailyBriefingPanel } from "./components/DailyBriefingPanel";
import { ContinuousDesk } from "./components/ContinuousDesk";
import { IngestionDashboard } from "./components/IngestionDashboard";
import { EvalDashboard } from "./components/EvalDashboard";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { ChatSidebar } from "./components/ChatSidebar";
import { ChatToggleBtn } from "./components/ChatToggleBtn";
import { Login } from "./components/Login";
import { ModeBanner } from "./components/ModeBanner";
import { useAuth } from "./auth/AuthContext";
import type { ProbabilityReport, Stage, MarketLive, AlertEntry } from "./types";
import { TICKERS } from "./types";
import {
  loadHistory, appendHistory, loadLastReport, saveLastReport,
  loadMarket as loadMarketCache, saveMarket as saveMarketCache,
  loadAlerts, appendAlert,
} from "./storage";

const REFRESH_MS = 90_000; // 90 sec auto-refresh

export default function App() {
  // ── Auth / RBAC ────────────────────────────────────────────────────────────
  const { isAuthenticated, isAdmin, user, logout } = useAuth();

  // ── Live market state ──────────────────────────────────────────────────────
  const [market, setMarket] = useState<MarketLive | null>(loadMarketCache());
  const [marketLoading, setMarketLoading] = useState(true);
  const [marketError, setMarketError] = useState<string | null>(null);
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // ── View mode ──────────────────────────────────────────────────────────────
  type ViewMode = "command" | "analysis" | "briefing" | "continuous" | "ingestion" | "eval";
  const [viewMode, setViewMode] = useState<ViewMode>("command");
  // ── Analysis panel state ───────────────────────────────────────────────────
  const [stage, setStage] = useState<Stage>("idle");
  const [report, setReport] = useState<ProbabilityReport | null>(loadLastReport());
  const [blocked, setBlocked] = useState<string[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [runId, setRunId] = useState<string | null>(null);
  const [selectedTicker, setSelectedTicker] = useState<string | null>(null);
  const [history, setHistory] = useState<ProbabilityReport[]>(loadHistory());
  const [alerts, setAlerts] = useState<AlertEntry[]>(loadAlerts());
  const [toastQueue, setToastQueue] = useState<AlertEntry[]>([]);
  const prevMarketRef = useRef<MarketLive | null>(loadMarketCache());

  // V2.0: Chat sidebar state
  const [chatOpen, setChatOpen] = useState(false);
  const [hasNewMsg, setHasNewMsg] = useState(false);
  const clearNewMsg = useCallback(() => setHasNewMsg(false), []);

  const fireAlert = useCallback((entry: AlertEntry) => {
    const next = appendAlert(entry);
    setAlerts(next);
    setToastQueue((q) => [...q, entry]);
  }, []);

  const loadMarket = useCallback(async () => {
    try {
      setMarketError(null);
      const data = await fetchMarketLive();
      const prev = prevMarketRef.current;
      if (prev) {
        if (prev.vix.regime !== data.vix.regime) {
          fireAlert({
            id: `${Date.now()}-regime`,
            at: new Date().toISOString(),
            kind: "regime",
            prev: prev.vix.regime,
            curr: data.vix.regime,
          });
        }
        if (prev.risk_summary.market_heat !== data.risk_summary.market_heat) {
          fireAlert({
            id: `${Date.now()}-heat`,
            at: new Date().toISOString(),
            kind: "heat",
            prev: prev.risk_summary.market_heat,
            curr: data.risk_summary.market_heat,
          });
        }
      }
      prevMarketRef.current = data;
      setMarket(data);
      saveMarketCache(data);
      setLastRefresh(new Date());
    } catch (e) {
      setMarketError(String(e));
    } finally {
      setMarketLoading(false);
    }
  }, [fireAlert]);

  useEffect(() => {
    loadMarket();
    timerRef.current = setInterval(loadMarket, REFRESH_MS);
    return () => { if (timerRef.current) clearInterval(timerRef.current); };
  }, [loadMarket]);

  async function run(params: AnalyzeParams) {
    setReport(null); setBlocked(null); setError(null); setRunId(null);
    try {
      const rpt = await analyze(params, setStage, setRunId);
      setReport(rpt);
      setHistory(appendHistory(rpt)); // persist to localStorage history
      saveLastReport(rpt);
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

  // Route protection: a standard user can never land on the admin-only INGEST
  // view, even via stale state. Coerce them back to the command center.
  const effectiveView: ViewMode =
    viewMode === "ingestion" && !isAdmin ? "command" : viewMode;

  // Auth gate — the entire desk is behind login. Placed after all hooks so the
  // hook order stays stable across renders.
  if (!isAuthenticated) {
    return <Login />;
  }

  const tape = [...TICKERS, ...TICKERS, ...TICKERS];

  return (
    <div className="desk">
      <div className="scanlines" aria-hidden="true" />

      {/* ── Header ─────────────────────────────────────────────────────────── */}
      <header className="masthead">
        <div className="masthead__row">
          <div>
            <h1>QST<span className="masthead__accent">_</span></h1>
            <p className="masthead__sub">QUANT SWARM TERMINAL · 360° MULTI-AGENT MARKET INTELLIGENCE</p>
          </div>
          <div className="masthead__controls">
            <button
              className={`mode-btn ${viewMode === "continuous" ? "mode-btn--on" : ""}`}
              onClick={() => setViewMode(m => m === "continuous" ? "command" : "continuous")}
            >
              {viewMode === "continuous" ? "◉ LIVE DESK" : "○ LIVE DESK"}
            </button>
            <button
              className={`mode-btn ${viewMode === "eval" ? "mode-btn--on" : ""}`}
              onClick={() => setViewMode(m => m === "eval" ? "command" : "eval")}
            >
              {viewMode === "eval" ? "◉ EVAL LAB" : "○ EVAL LAB"}
            </button>
            {isAdmin && (
              <button
                className={`mode-btn ${viewMode === "ingestion" ? "mode-btn--on" : ""}`}
                onClick={() => setViewMode(m => m === "ingestion" ? "command" : "ingestion")}
              >
                {viewMode === "ingestion" ? "◉ INGEST" : "○ INGEST"}
              </button>
            )}
            <button
              className={`mode-btn ${viewMode === "briefing" ? "mode-btn--on" : ""}`}
              onClick={() => setViewMode(m => m === "briefing" ? "command" : "briefing")}
            >
              {viewMode === "briefing" ? "◉ BRIEFING" : "○ BRIEFING"}
            </button>
            <button
              className={`mode-btn ${viewMode === "analysis" ? "mode-btn--on" : ""}`}
              onClick={() => setViewMode(m => m === "analysis" ? "command" : "analysis")}
            >
              {viewMode === "analysis" ? "◉ ANALYSIS MODE" : "○ ANALYSIS MODE"}
            </button>
            <button
              className="refresh-btn mono"
              onClick={() => { setMarketLoading(true); loadMarket(); }}
              disabled={marketLoading}
            >
              {marketLoading ? "LOADING…" : "⟳ REFRESH"}
            </button>
            {lastRefresh && (
              <span className="refresh-ts mono">
                Updated {lastRefresh.toLocaleTimeString()}
              </span>
            )}
            <span className="user-badge mono" title={`Role: ${user?.role}`}>
              <i className={`user-badge__dot user-badge__dot--${user?.role}`} />
              {user?.username}
              <em className="user-badge__role">{user?.role}</em>
            </span>
            <button className="logout-btn mono" onClick={logout} title="Sign out">
              ⎋ LOGOUT
            </button>
          </div>
        </div>

        {/* Ticker tape */}
        <div className="tape" aria-hidden="true">
          <div className="tape__inner">
            {tape.map((t, i) => {
              const sig = market?.tickers[t];
              const chg = sig?.change_pct ?? 0;
              return (
                <span key={i} className="tape__item mono">
                  {t}{" "}
                  {sig ? (
                    <i className={chg >= 0 ? "up" : "dn"}>
                      {chg >= 0 ? "▲" : "▼"} {Math.abs(chg).toFixed(2)}%
                    </i>
                  ) : (
                    <i className={i % 3 === 1 ? "dn" : "up"}>{i % 3 === 1 ? "▼" : "▲"}</i>
                  )}
                </span>
              );
            })}
          </div>
        </div>
      </header>

      {error && <div className="errorbar mono">✕ {error}</div>}

      <AlertToast
        alerts={toastQueue}
        onDismiss={(id) => setToastQueue((q) => q.filter((a) => a.id !== id))}
      />

      {/* ── Main content (each view isolated by an ErrorBoundary) ──────────── */}
      {effectiveView === "analysis" ? (
        <ErrorBoundary name="Analysis">
          <ModeBanner title="Analysis — Deep-Dive Desk" source="live">
            On-demand single-ticker research: live <b>MCP</b> technical + fundamental data,
            optional <b>chart vision</b> (upload a chart), and the full 7-agent crew.
          </ModeBanner>
          {alerts.length > 0 && <AlertLog alerts={alerts} />}
          <HistoryBar
            items={history}
            currentRunId={report?.run_id ?? null}
            onSelect={(r) => {
              setReport(r);
              setRunId(r.run_id);
              setBlocked(null);
              setError(null);
              setStage("done");
            }}
          />
          <main className="desk__grid">
            <RequestForm stage={stage} onSubmit={run} initialTicker={selectedTicker} />
            <ReportView report={report} blocked={blocked} />
            <AgentLog runId={runId} />
          </main>
        </ErrorBoundary>
      ) : effectiveView === "briefing" ? (
        <ErrorBoundary name="Daily Briefing"><DailyBriefingPanel /></ErrorBoundary>
      ) : effectiveView === "continuous" ? (
        <ErrorBoundary name="Live Desk"><ContinuousDesk /></ErrorBoundary>
      ) : effectiveView === "ingestion" ? (
        <ErrorBoundary name="Ingestion Dashboard"><IngestionDashboard /></ErrorBoundary>
      ) : effectiveView === "eval" ? (
        <ErrorBoundary name="EVAL Research Lab"><EvalDashboard /></ErrorBoundary>
      ) : (
        <ErrorBoundary name="Command Center">
          <CommandCenter
            market={market}
            loading={marketLoading}
            error={marketError}
            onRunAnalysis={(ticker) => {
              setSelectedTicker(ticker);
              setViewMode("analysis");
            }}
          />
        </ErrorBoundary>
      )}

      <footer className="colophon mono">
        rag:8001 · vision:8002 · agents:8003 · rails:8004
        {" — "}
        <span style={{ color: "var(--amber)" }}>
          Auto-refresh every 90s
        </span>
        {" — "}
        reports are probabilistic estimates, never guarantees
      </footer>

      {/* V2.0: Global Chat Sidebar — accessible from all views */}
      <ChatToggleBtn
        isOpen={chatOpen}
        hasNewMsg={hasNewMsg}
        onClick={() => setChatOpen((o) => !o)}
      />
      <ChatSidebar
        isOpen={chatOpen}
        onClose={() => setChatOpen(false)}
        hasNewMsg={hasNewMsg}
        clearNewMsg={clearNewMsg}
      />
    </div>
  );
}
