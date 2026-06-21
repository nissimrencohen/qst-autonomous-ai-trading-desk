/**
 * IngestionDashboard — V2.0 dual-tab view for the INGEST panel.
 *
 * Tab 1 — PIPELINE STATUS: automated ingestion engine health (original).
 * Tab 2 — MANUAL UPLOAD: migrated admin features (single doc, bulk JSON, vision).
 */
import { useCallback, useEffect, useState } from "react";
import { fetchIngestionStatus } from "../api";
import type { IngestionStatus } from "../types";
import { ManualUploadPanel } from "./ManualUploadPanel";

const POLL_MS = 15_000;

type IngestView = "pipeline" | "manual";

function ageLabel(iso: string | null): string {
  if (!iso) return "—";
  const secs = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (secs < 90) return `${Math.round(secs)}s ago`;
  if (secs < 5400) return `${Math.round(secs / 60)}m ago`;
  return `${Math.round(secs / 3600)}h ago`;
}

const SOURCE_LABELS: Record<string, string> = {
  quote: "Quotes", news: "News (yfinance)", tavily_news: "News (Tavily)",
  ta_signal: "TA Signals", competitor: "Competitors", macro: "Macro / VIX", social: "Social",
};

function StatCard({ label, value, sub }: { label: string; value: string | number; sub?: string }) {
  return (
    <div style={{
      border: "1px solid var(--border-default,#334155)", borderRadius: 4,
      padding: "10px 14px", minWidth: 120,
    }}>
      <div style={{ fontSize: 22, fontWeight: "bold", color: "var(--text-primary,#e2e8f0)" }}>{value}</div>
      <div style={{ fontSize: 10, letterSpacing: 1, color: "var(--text-tertiary,#64748b)", textTransform: "uppercase" }}>{label}</div>
      {sub && <div style={{ fontSize: 9, color: "var(--text-tertiary,#64748b)" }}>{sub}</div>}
    </div>
  );
}

function PipelineStatus() {
  const [data, setData] = useState<IngestionStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [lastFetch, setLastFetch] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const d = await fetchIngestionStatus();
      if (d) { setData(d); setLastFetch(new Date().toLocaleTimeString()); }
    } catch { /* keep last good */ } finally { setLoading(false); }
  }, []);

  useEffect(() => {
    load();
    const id = setInterval(load, POLL_MS);
    return () => clearInterval(id);
  }, [load]);

  const sources = data ? Object.entries(data.by_source_type) : [];
  const tickers = data?.by_ticker?.filter(t => !["MACRO", "VIX"].includes(t.ticker)) ?? [];

  return (
    <div style={{ fontFamily: "'Consolas','Monaco',monospace", padding: "4px 2px" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 16, flexWrap: "wrap", marginBottom: 14, fontSize: 11 }}>
        <span style={{ letterSpacing: 2, color: "var(--text-primary,#e2e8f0)", textTransform: "uppercase" }}>
          Ingestion Engine
        </span>
        <span style={{
          fontSize: 10, padding: "2px 7px", borderRadius: 2, letterSpacing: 1,
          background: data?.enabled ? "rgba(34,197,94,.15)" : "rgba(100,116,139,.15)",
          color: data?.enabled ? "#22c55e" : "#64748b",
        }}>
          {data?.enabled ? `● RUNNING (every ${data.interval_s ?? 60}s)` : "○ DISABLED"}
        </span>
        <span style={{ color: "var(--text-tertiary,#64748b)" }}>
          last ingest: {ageLabel(data?.latest_ingested_at ?? null)}
        </span>
        {lastFetch && <span style={{ color: "var(--text-tertiary,#64748b)" }}>polled {lastFetch}</span>}
        <span style={{ flex: 1 }} />
        <button onClick={load} style={{
          padding: "4px 8px", fontSize: 10, border: "1px solid var(--border-default,#334155)",
          borderRadius: 3, cursor: "pointer", background: "transparent", color: "var(--text-tertiary,#64748b)",
        }}>↻ REFRESH</button>
      </div>

      {loading && !data && (
        <div style={{ color: "var(--text-tertiary,#64748b)", fontSize: 12, padding: "24px 0", textAlign: "center" }}>
          Loading ingestion status…
        </div>
      )}

      {data?.error && (
        <div style={{ border: "1px solid var(--amber,#f59e0b)", borderRadius: 4, padding: 12, marginBottom: 12, fontSize: 11, color: "var(--amber,#f59e0b)" }}>
          Cache not readable yet: {data.error}
        </div>
      )}

      {data && (
        <>
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginBottom: 14 }}>
            <StatCard label="Total rows" value={data.total} />
            {sources.map(([src, n]) => (
              <StatCard key={src} label={SOURCE_LABELS[src] ?? src} value={n} />
            ))}
          </div>

          {tickers.length > 0 ? (
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(150px, 1fr))", gap: 8 }}>
              {tickers.map(t => (
                <div key={t.ticker} style={{
                  border: "1px solid var(--border-default,#334155)", borderRadius: 4,
                  padding: "8px 12px", display: "flex", justifyContent: "space-between", alignItems: "center",
                }}>
                  <div>
                    <div style={{ fontSize: 13, fontWeight: "bold", letterSpacing: 1, color: "var(--text-primary,#e2e8f0)" }}>{t.ticker}</div>
                    <div style={{ fontSize: 9, color: "var(--text-tertiary,#64748b)" }}>{ageLabel(t.latest)}</div>
                  </div>
                  <div style={{ fontSize: 16, color: t.rows > 0 ? "#22c55e" : "#64748b" }}>{t.rows}</div>
                </div>
              ))}
            </div>
          ) : (
            <div style={{
              border: "1px dashed var(--border-default,#334155)", borderRadius: 4,
              padding: 22, textAlign: "center", color: "var(--text-tertiary,#64748b)", fontSize: 12,
            }}>
              <div style={{ marginBottom: 8 }}>The ingestion cache is empty.</div>
              <div style={{ fontSize: 11 }}>
                Enable the engine with <code style={{ color: "var(--amber,#f59e0b)" }}>AGENTIC_INGESTION_ENABLED=true</code>{" "}
                — it fetches quotes, news, TA, competitors and macro/VIX every minute.
              </div>
            </div>
          )}

          <div style={{ marginTop: 14, fontSize: 10, color: "var(--text-tertiary,#64748b)", borderTop: "1px solid var(--border-default,#334155)", paddingTop: 8 }}>
            Structured SQLite cache the offline synthesis loop reads from — no live API calls during synthesis.
          </div>
        </>
      )}
    </div>
  );
}

export function IngestionDashboard() {
  const [view, setView] = useState<IngestView>("pipeline");

  const tabs: { id: IngestView; label: string; desc: string }[] = [
    { id: "pipeline", label: "⬡ PIPELINE STATUS", desc: "Automated ingestion engine health" },
    { id: "manual",   label: "⊕ MANUAL UPLOAD",   desc: "Admin: ingest docs, vision quick-score" },
  ];

  return (
    <main style={{ fontFamily: "'Consolas','Monaco',monospace", padding: "4px 2px" }}>
      {/* Sub-tab navigation */}
      <div style={{
        display: "flex",
        gap: 8,
        marginBottom: 18,
        borderBottom: "1px solid var(--line,#1c2630)",
        paddingBottom: 10,
      }}>
        {tabs.map((t) => (
          <button
            key={t.id}
            id={`ingest-tab-${t.id}`}
            onClick={() => setView(t.id)}
            title={t.desc}
            style={{
              fontFamily: "var(--font-display,'Saira Condensed',sans-serif)",
              fontSize: 12,
              fontWeight: 600,
              letterSpacing: "0.12em",
              padding: "7px 16px",
              background: view === t.id ? "var(--amber,#ffb454)" : "transparent",
              color: view === t.id ? "#0a0e12" : "var(--ink-dim,#7d8c99)",
              border: view === t.id
                ? "1px solid var(--amber,#ffb454)"
                : "1px solid var(--line-bright,#2a3845)",
              cursor: "pointer",
              transition: "all 0.15s",
              boxShadow: view === t.id ? "0 0 14px rgba(255,180,84,0.25)" : "none",
            }}
          >
            {t.label}
          </button>
        ))}
        <span style={{ flex: 1 }} />
        <span style={{ fontSize: 10, color: "var(--ink-dim,#7d8c99)", alignSelf: "center" }}>
          {view === "manual" ? "Manual admin tools — write directly to RAG corpus" : "Automated 1-min ingestion engine"}
        </span>
      </div>

      {view === "pipeline" && <PipelineStatus />}
      {view === "manual"   && <ManualUploadPanel />}
    </main>
  );
}
