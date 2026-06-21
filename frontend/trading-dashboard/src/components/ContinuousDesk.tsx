/**
 * ContinuousDesk — "Live Continuous Desk" view (Step 2g).
 *
 * Polls GET /synthesis/latest every 30s and renders the autonomously generated
 * report for each of the 10 watchlist tickers, straight from the SQLite report
 * store (no analysis request needed). Each card surfaces the Macro/VIX context
 * block embedded in the continuous report (Data Integrity requirement).
 */
import { useCallback, useEffect, useState } from "react";
import { fetchSynthesisLatest, fetchSynthesisStatus } from "../api";
import { ModeBanner } from "./ModeBanner";
import type { SynthesisReport, SynthesisStatus } from "../types";

const POLL_MS = 30_000;

function pct(v: number | null | undefined, d = 0): string {
  return v == null ? "—" : (v * 100).toFixed(d) + "%";
}

function ageLabel(iso: string): string {
  const secs = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (secs < 90) return `${Math.round(secs)}s ago`;
  if (secs < 5400) return `${Math.round(secs / 60)}m ago`;
  return `${Math.round(secs / 3600)}h ago`;
}

const REGIME_COLOR: Record<string, string> = {
  calm: "#22c55e", elevated: "#f59e0b", stress: "#ef4444", panic: "#9333ea", unknown: "#64748b",
};
function bullColor(b: number): string {
  return b >= 0.6 ? "#22c55e" : b >= 0.4 ? "#f59e0b" : "#ef4444";
}
function sideLabel(s: string): string {
  return s === "long" ? "↑ LONG" : s === "short" ? "↓ SHORT" : "— FLAT";
}
function sideColor(s: string): string {
  return s === "long" ? "#22c55e" : s === "short" ? "#ef4444" : "#64748b";
}
function changeColor(v: number | null | undefined): string {
  if (v == null) return "#64748b";
  return v > 0 ? "#22c55e" : v < 0 ? "#ef4444" : "#64748b";
}

// ── Macro/VIX context block (Data Integrity) ──────────────────────────────────

function MacroBlock({ macro }: { macro: SynthesisReport["macro"] }) {
  const m = macro?.macro;
  const v = macro?.vix;
  const regime = v?.regime ?? "unknown";
  const rc = REGIME_COLOR[regime] ?? "#64748b";
  const fmt = (leg?: { price: number; change_pct: number } | null) =>
    leg ? `${leg.price} (${leg.change_pct >= 0 ? "+" : ""}${leg.change_pct.toFixed(2)}%)` : "n/a";

  return (
    <div style={{
      border: `1px solid ${rc}44`, background: `${rc}08`, borderRadius: 4,
      padding: "6px 10px", marginBottom: 8, fontSize: 10,
    }}>
      <div style={{ letterSpacing: 1, color: "var(--text-tertiary,#64748b)", marginBottom: 3 }}>
        MACRO &amp; FEAR CONTEXT
      </div>
      {m && !m.error ? (
        <div style={{ color: "var(--text-secondary,#94a3b8)" }}>
          S&amp;P <span style={{ color: changeColor(m.sp500?.change_pct) }}>{fmt(m.sp500)}</span>
          {"  ·  "}NASDAQ <span style={{ color: changeColor(m.nasdaq?.change_pct) }}>{fmt(m.nasdaq)}</span>
          {m.market_tone && <span style={{ color: "var(--text-tertiary,#64748b)" }}>{"  → "}{m.market_tone}</span>}
        </div>
      ) : (
        <div style={{ color: "#64748b" }}>broad market unavailable</div>
      )}
      {v && !v.error ? (
        <div style={{ marginTop: 2 }}>
          <span style={{ color: "var(--text-tertiary,#64748b)" }}>VIX </span>
          <span style={{ color: rc, fontWeight: "bold" }}>{v.vix_30d != null ? v.vix_30d.toFixed(1) : "—"}</span>
          <span style={{ color: "var(--text-tertiary,#64748b)" }}> · {v.term_structure} · </span>
          <span style={{
            color: rc, fontWeight: "bold", letterSpacing: 1,
            padding: "0 5px", borderRadius: 2, background: `${rc}22`,
          }}>{regime.toUpperCase()}</span>
        </div>
      ) : (
        <div style={{ color: "#64748b" }}>fear index unavailable</div>
      )}
    </div>
  );
}

// ── per-ticker card ───────────────────────────────────────────────────────────

function DeskCard({ item }: { item: SynthesisReport }) {
  const r = item.report;
  const p = r.probabilities;
  const risk = r.risk_assessment?.risk_level;
  const ep = r.execution_plan;
  const vv = r.volatility_view;
  const isVol = ["VIXY", "SVXY"].includes(item.ticker);

  return (
    <div style={{
      border: `1px solid ${isVol ? "rgba(168,85,247,.3)" : "var(--border-default,#334155)"}`,
      borderRadius: 4, padding: "10px 12px",
      background: isVol ? "rgba(168,85,247,.03)" : "transparent",
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
        <div>
          <span style={{ fontSize: 14, fontWeight: "bold", letterSpacing: 1, color: "var(--text-primary,#e2e8f0)" }}>
            {item.ticker}
          </span>
          {isVol && <span style={{ fontSize: 9, color: "#a855f7", marginLeft: 5, letterSpacing: 1 }}>VOL</span>}
          <span style={{ fontSize: 9, color: "var(--text-tertiary,#64748b)", marginLeft: 6 }}>
            {ageLabel(item.generated_at)}
          </span>
        </div>
        <div>
          <span style={{ fontSize: 11, fontWeight: "bold", color: sideColor(ep?.side ?? "flat") }}>
            {sideLabel(ep?.side ?? "flat")}
          </span>
          {risk && (
            <span style={{
              fontSize: 9, marginLeft: 6, padding: "1px 5px", borderRadius: 2,
              background: { low: "rgba(34,197,94,.15)", medium: "rgba(245,158,11,.15)", high: "rgba(239,68,68,.15)" }[risk],
              color: { low: "#22c55e", medium: "#f59e0b", high: "#ef4444" }[risk],
            }}>{risk.toUpperCase()}</span>
          )}
        </div>
      </div>

      {/* Macro/VIX context block embedded in the continuous report */}
      <MacroBlock macro={item.macro} />

      {/* Directional probabilities */}
      <div style={{ display: "flex", gap: 10, marginBottom: 6, fontSize: 11 }}>
        <div style={{ textAlign: "center" }}>
          <div style={{ color: bullColor(p.bullish), fontWeight: "bold" }}>{pct(p.bullish)}</div>
          <div style={{ color: "var(--text-tertiary,#64748b)", fontSize: 9 }}>BULL</div>
        </div>
        <div style={{ textAlign: "center" }}>
          <div style={{ color: "#64748b", fontWeight: "bold" }}>{pct(p.neutral)}</div>
          <div style={{ color: "var(--text-tertiary,#64748b)", fontSize: 9 }}>NEUT</div>
        </div>
        <div style={{ textAlign: "center" }}>
          <div style={{ color: "#ef4444", fontWeight: "bold" }}>{pct(p.bearish)}</div>
          <div style={{ color: "var(--text-tertiary,#64748b)", fontSize: 9 }}>BEAR</div>
        </div>
        <div style={{ flex: 1 }} />
        <div style={{ textAlign: "right", fontSize: 10, color: "var(--text-tertiary,#64748b)" }}>
          conf {pct(r.confidence)}
        </div>
      </div>

      {vv && vv.regime && vv.regime !== "unknown" && (
        <div style={{ fontSize: 10, color: "var(--text-tertiary,#64748b)", marginBottom: 4 }}>
          Vol read: {vv.regime} · {vv.term_structure}
          {vv.signal ? ` · ${vv.signal.slice(0, 60)}` : ""}
        </div>
      )}

      {ep?.entry != null && (
        <div style={{ fontSize: 10, color: "var(--text-tertiary,#64748b)", borderTop: "1px solid rgba(51,65,85,.5)", paddingTop: 5, marginTop: 4 }}>
          Entry {ep.entry} · Stop {ep.stop_loss ?? "—"} · Target {ep.target ?? "—"}
          {ep.risk_reward_ratio != null && ` · R:R ${ep.risk_reward_ratio.toFixed(1)}`}
          {ep.paper_only && <span style={{ color: "#f59e0b" }}> · PAPER</span>}
        </div>
      )}

      {r.caveats?.[0] && (
        <div style={{ fontSize: 9, color: "var(--text-tertiary,#64748b)", marginTop: 5, fontStyle: "italic" }}>
          ⚠ {r.caveats[0].slice(0, 110)}
        </div>
      )}
    </div>
  );
}

// ── main view ───────────────────────────────────────────────────────────────

export function ContinuousDesk() {
  const [reports, setReports] = useState<SynthesisReport[]>([]);
  const [status, setStatus] = useState<SynthesisStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [lastFetch, setLastFetch] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [latest, st] = await Promise.all([fetchSynthesisLatest(), fetchSynthesisStatus()]);
      // newest first
      setReports([...latest.reports].sort((a, b) => b.updated_at.localeCompare(a.updated_at)));
      setStatus(st);
      setLastFetch(new Date().toLocaleTimeString());
    } catch {
      /* transient — keep last good data */
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const id = setInterval(load, POLL_MS);
    return () => clearInterval(id);
  }, [load]);

  const hb = status?.heartbeat ? ageLabel(status.heartbeat) : "—";

  return (
    <main style={{ fontFamily: "'Consolas','Monaco',monospace", padding: "4px 2px" }}>
      <ModeBanner title="Live Desk — Continuous Monitor" source="cached">
        Always-on synthesis across all 10 tickers from the 1-minute ingestion cache.
        Fast and budget-safe — for a live MCP deep-dive with chart vision, use <b>Analysis</b>.
      </ModeBanner>
      {/* Status bar */}
      <div style={{ display: "flex", alignItems: "center", gap: 16, flexWrap: "wrap", marginBottom: 14, fontSize: 11 }}>
        <span style={{ letterSpacing: 2, color: "var(--text-primary,#e2e8f0)", textTransform: "uppercase" }}>
          Live Continuous Desk
        </span>
        <span style={{
          fontSize: 10, padding: "2px 7px", borderRadius: 2, letterSpacing: 1,
          background: reports.length ? "rgba(34,197,94,.15)" : "rgba(100,116,139,.15)",
          color: reports.length ? "#22c55e" : "#64748b",
        }}>
          {reports.length ? `● ${reports.length}/10 LIVE` : "○ IDLE"}
        </span>
        <span style={{ color: "var(--text-tertiary,#64748b)" }}>
          last synth: {status?.last_ticker ?? "—"} ({status?.last_status ?? "—"}) · {hb}
        </span>
        {lastFetch && <span style={{ color: "var(--text-tertiary,#64748b)" }}>polled {lastFetch}</span>}
        <span style={{ flex: 1 }} />
        <button
          onClick={load}
          style={{
            padding: "4px 8px", fontSize: 10, border: "1px solid var(--border-default,#334155)",
            borderRadius: 3, cursor: "pointer", background: "transparent", color: "var(--text-tertiary,#64748b)",
          }}
        >↻ REFRESH</button>
      </div>

      {loading && reports.length === 0 && (
        <div style={{ color: "var(--text-tertiary,#64748b)", fontSize: 12, padding: "24px 0", textAlign: "center" }}>
          Loading continuous desk…
        </div>
      )}

      {!loading && reports.length === 0 && (
        <div style={{
          border: "1px dashed var(--border-default,#334155)", borderRadius: 4,
          padding: 22, textAlign: "center", color: "var(--text-tertiary,#64748b)", fontSize: 12,
        }}>
          <div style={{ marginBottom: 8 }}>No continuous reports yet.</div>
          <div style={{ fontSize: 11 }}>
            The synthesis loop is opt-in. Enable it with{" "}
            <code style={{ color: "var(--amber,#f59e0b)" }}>AGENTIC_SYNTHESIS_LOOP_ENABLED=true</code>{" "}
            (and the ingestion engine) to populate the desk autonomously.
          </div>
        </div>
      )}

      {reports.length > 0 && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))", gap: 10 }}>
          {reports.map((item) => <DeskCard key={item.ticker} item={item} />)}
        </div>
      )}

      <div style={{ marginTop: 14, fontSize: 10, color: "var(--text-tertiary,#64748b)", borderTop: "1px solid var(--border-default,#334155)", paddingTop: 8 }}>
        Reports are generated autonomously every ~2.5 min/ticker from the SQLite ingestion cache — no live API calls during synthesis. Probabilistic estimates, never guarantees.
      </div>
    </main>
  );
}
