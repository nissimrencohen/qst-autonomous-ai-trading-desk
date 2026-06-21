/**
 * DailyBriefingPanel — Morning briefing output for all whitelisted instruments.
 *
 * Shows for each instrument:
 *  • P(>2% move) and P(>5% move) for the selected horizon
 *  • Crew directional bias (bull/bear/neutral %)
 *  • Overnight gap + 30-min momentum
 *  • Execution side + R:R
 *  • Daily vol range (±1σ)
 *
 * VIX regime banner at the top with market-wide move probs.
 * Styling is aligned to the QST phosphor-amber / neo-mint terminal palette.
 */

import { useEffect, useState, useCallback } from "react";
import { fetchDailyBriefing, triggerDailyBriefing } from "../api";
import { ModeBanner } from "./ModeBanner";
import type { DailyBriefing, InstrumentBriefing, MoveProbs } from "../types";

// ── palette (single source of truth — mirrors styles.css :root) ────────────────
const C = {
  ink: "var(--ink)",
  dim: "var(--ink-dim)",
  line: "var(--line)",
  lineHi: "var(--line-bright)",
  amber: "var(--amber)",
  bull: "var(--bull)",
  bear: "var(--bear)",
  flat: "var(--flat)",
  extreme: "#d946ef", // reserved for >10% moves / panic regime only
} as const;

// ── helpers ───────────────────────────────────────────────────────────────────

function pct(v: number | null | undefined, decimals = 0): string {
  if (v == null) return "—";
  return (v * 100).toFixed(decimals) + "%";
}

function num(v: number | null | undefined, d = 2): string {
  if (v == null) return "—";
  return v.toFixed(d);
}

function gapColor(g: number | null): string {
  if (g == null) return C.dim;
  return g > 0 ? C.bull : g < 0 ? C.bear : C.dim;
}

function regimeColor(r: string): string {
  return { calm: C.bull, elevated: C.amber, stress: C.bear, panic: C.extreme }[r] ?? C.dim;
}

function bullColor(b: number): string {
  if (b >= 0.6) return C.bull;
  if (b >= 0.4) return C.amber;
  return C.bear;
}

function sideLabel(side: string): string {
  return side === "long" ? "↑ LONG" : side === "short" ? "↓ SHORT" : "— FLAT";
}

function sideColor(side: string): string {
  return side === "long" ? C.bull : side === "short" ? C.bear : C.dim;
}

// ── mini prob bar ─────────────────────────────────────────────────────────────

function ProbBar({ label, value, color }: { label: string; value: number | null | undefined; color: string }) {
  const v = value ?? 0;
  const w = Math.min(100, v * 100);
  return (
    <div style={{ marginBottom: 5 }}>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10, color: C.dim, marginBottom: 2 }}>
        <span>{label}</span>
        <span style={{ color, fontWeight: "bold" }}>{(v * 100).toFixed(0)}%</span>
      </div>
      <div style={{ height: 4, background: "var(--bg)", border: `1px solid ${C.line}`, borderRadius: 2, overflow: "hidden" }}>
        <div style={{ width: `${w}%`, height: "100%", background: color, borderRadius: 2 }} />
      </div>
    </div>
  );
}

// ── instrument card ───────────────────────────────────────────────────────────

function InstrumentCard({ inst, horizon }: { inst: InstrumentBriefing; horizon: "1d" | "5d" }) {
  const probs: MoveProbs = horizon === "1d" ? inst.move_probs_1d : inst.move_probs_5d;
  const crew = inst.crew;
  const isVol = ["VIXY", "SVXY"].includes(inst.ticker);

  const moveColor = (p: number) => (p >= 0.35 ? C.bear : p >= 0.2 ? C.amber : C.bull);

  return (
    <div style={{
      border: `1px solid ${isVol ? "rgba(255,180,84,.32)" : C.line}`,
      borderRadius: 4,
      padding: "14px 16px",
      background: isVol ? "rgba(255,180,84,.04)" : "var(--bg-card)",
      display: "flex",
      flexDirection: "column",
      gap: 4,
    }}>
      {/* Header row */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
        <div>
          <span style={{ fontFamily: "var(--font-display)", fontSize: 15, fontWeight: 700, color: C.ink, letterSpacing: ".08em" }}>
            {inst.ticker}
          </span>
          {isVol && <span style={{ fontSize: 9, color: C.amber, marginLeft: 6, letterSpacing: ".12em" }}>VOL</span>}
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <span style={{ fontSize: 11, fontWeight: "bold", color: sideColor(crew.execution_side) }}>
            {sideLabel(crew.execution_side)}
          </span>
          {crew.risk_level && (
            <span style={{
              fontSize: 9, padding: "1px 6px", borderRadius: 2, letterSpacing: ".08em",
              background: { low: "rgba(45,212,167,.15)", medium: "rgba(255,180,84,.15)", high: "rgba(255,93,93,.15)" }[crew.risk_level],
              color: { low: C.bull, medium: C.amber, high: C.bear }[crew.risk_level],
            }}>
              {crew.risk_level.toUpperCase()}
            </span>
          )}
        </div>
      </div>

      {/* Crew probabilities */}
      <div style={{ display: "flex", gap: 10, marginBottom: 8, fontSize: 11 }}>
        <div style={{ textAlign: "center" }}>
          <div style={{ color: bullColor(crew.bullish ?? 0), fontWeight: "bold" }}>{pct(crew.bullish)}</div>
          <div style={{ color: C.dim, fontSize: 9 }}>BULL</div>
        </div>
        <div style={{ textAlign: "center" }}>
          <div style={{ color: C.flat, fontWeight: "bold" }}>{pct(crew.neutral)}</div>
          <div style={{ color: C.dim, fontSize: 9 }}>NEUT</div>
        </div>
        <div style={{ textAlign: "center" }}>
          <div style={{ color: C.bear, fontWeight: "bold" }}>{pct(crew.bearish)}</div>
          <div style={{ color: C.dim, fontSize: 9 }}>BEAR</div>
        </div>
        <div style={{ flex: 1 }} />
        {inst.overnight_gap_pct != null && (
          <div style={{ textAlign: "right", fontSize: 10 }}>
            <span style={{ color: C.dim }}>Gap </span>
            <span style={{ color: gapColor(inst.overnight_gap_pct), fontWeight: "bold" }}>
              {inst.overnight_gap_pct > 0 ? "+" : ""}{inst.overnight_gap_pct.toFixed(2)}%
            </span>
          </div>
        )}
        {inst.intraday_30m && (
          <div style={{ textAlign: "right", fontSize: 10 }}>
            <span style={{ color: C.dim }}>30m </span>
            <span style={{ color: gapColor(inst.intraday_30m.momentum_pct), fontWeight: "bold" }}>
              {inst.intraday_30m.momentum_pct > 0 ? "+" : ""}{inst.intraday_30m.momentum_pct.toFixed(2)}%
            </span>
          </div>
        )}
      </div>

      {/* Move probability bars */}
      <ProbBar label="P(|move| >2%)" value={probs.p_move_2pct} color={moveColor(probs.p_move_2pct)} />
      <ProbBar label="P(|move| >5%)" value={probs.p_move_5pct} color={moveColor(probs.p_move_5pct)} />
      {probs.p_move_10pct > 0.05 && (
        <ProbBar label="P(|move| >10%)" value={probs.p_move_10pct} color={C.extreme} />
      )}

      {/* Up vs Down split */}
      <div style={{ display: "flex", gap: 12, fontSize: 10, marginTop: 6, color: C.dim }}>
        <span style={{ color: C.bull }}>↑{((probs.p_up_2pct ?? 0) * 100).toFixed(0)}%</span>
        <span>vs</span>
        <span style={{ color: C.bear }}>↓{((probs.p_down_2pct ?? 0) * 100).toFixed(0)}%</span>
        <span style={{ flex: 1 }} />
        <span>±1σ {num(probs.expected_daily_range_pct, 1)}%/day</span>
      </div>

      {/* Execution levels */}
      {crew.entry && (
        <div style={{ marginTop: 8, fontSize: 10, color: C.dim, borderTop: `1px solid ${C.line}`, paddingTop: 8 }}>
          <span>Entry {num(crew.entry)} · Stop {num(crew.stop_loss)} · Target {num(crew.target)}</span>
          {crew.risk_reward && (
            <span style={{ marginLeft: 8, color: crew.risk_reward >= 2 ? C.bull : C.amber }}>
              R:R {crew.risk_reward.toFixed(1)}
            </span>
          )}
        </div>
      )}
    </div>
  );
}

// ── VIX regime banner ─────────────────────────────────────────────────────────

function VixBanner({ briefing, horizon }: { briefing: DailyBriefing; horizon: "1d" | "5d" }) {
  const ctx = briefing.market_context;
  const imp = horizon === "1d" ? ctx.vix_implied_1d : ctx.vix_implied_5d;
  const rc = regimeColor(ctx.regime);

  return (
    <div style={{
      border: `1px solid ${C.lineHi}`,
      borderLeft: `3px solid ${rc}`,
      borderRadius: 4,
      padding: "10px 16px",
      background: "var(--bg-raise)",
      marginBottom: 16,
      display: "flex",
      alignItems: "center",
      gap: 24,
      flexWrap: "wrap",
    }}>
      <div>
        <span style={{ fontFamily: "var(--font-mono)", fontSize: 20, fontWeight: 700, color: rc }}>{ctx.vix.toFixed(1)}</span>
        <span style={{ fontSize: 11, color: rc, marginLeft: 6 }}>VIX</span>
        <span style={{
          fontSize: 10, padding: "2px 8px", borderRadius: 2, marginLeft: 8,
          background: "var(--bg)", border: `1px solid ${rc}`, color: rc, fontWeight: "bold", letterSpacing: ".1em",
        }}>{ctx.regime.toUpperCase()}</span>
      </div>
      <div style={{ fontSize: 11, color: C.ink }}>
        <span style={{ color: C.dim }}>VIX-implied P(SPY &gt;2%) = </span>
        <span style={{ fontWeight: "bold", color: imp.p_move_2pct >= 0.3 ? C.bear : C.amber }}>
          {(imp.p_move_2pct * 100).toFixed(0)}%
        </span>
        <span style={{ marginLeft: 16, color: C.dim }}>P(SPY &gt;5%) = </span>
        <span style={{ fontWeight: "bold", color: imp.p_move_5pct >= 0.15 ? C.bear : C.dim }}>
          {(imp.p_move_5pct * 100).toFixed(0)}%
        </span>
      </div>
      <div style={{ flex: 1 }} />
      <div style={{ fontSize: 10, color: C.dim, letterSpacing: ".04em" }}>
        {briefing.briefing_date} · {briefing.generated_at.slice(11, 16)} UTC
      </div>
    </div>
  );
}

// ── main panel ────────────────────────────────────────────────────────────────

export function DailyBriefingPanel() {
  const [briefing, setBriefing] = useState<DailyBriefing | null>(null);
  const [loading, setLoading] = useState(true);
  const [triggering, setTriggering] = useState(false);
  const [horizon, setHorizon] = useState<"1d" | "5d">("1d");
  const [lastFetch, setLastFetch] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await fetchDailyBriefing();
      setBriefing(data);
      setLastFetch(new Date().toLocaleTimeString("en-US"));
    } catch {
      /* silent */
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const interval = setInterval(load, 5 * 60 * 1000); // refresh every 5 min
    return () => clearInterval(interval);
  }, [load]);

  const handleTrigger = async () => {
    setTriggering(true);
    try {
      await triggerDailyBriefing();
      // Poll for result every 20s for up to 5 minutes
      let attempts = 0;
      const poll = setInterval(async () => {
        attempts++;
        await load();
        if (attempts >= 15) clearInterval(poll);
      }, 20000);
    } finally {
      setTimeout(() => setTriggering(false), 3000);
    }
  };

  const S: React.CSSProperties = {
    fontFamily: "var(--font-mono)",
    fontSize: 13,
    fontVariantNumeric: "tabular-nums",
  };

  const toggleBtn = (active: boolean): React.CSSProperties => ({
    padding: "4px 12px", fontSize: 10, cursor: "pointer", border: "none",
    background: active ? "var(--amber)" : "transparent",
    color: active ? "#0a0e12" : C.dim,
    letterSpacing: ".1em", fontWeight: active ? 700 : 400,
    fontFamily: "var(--font-display)",
  });

  return (
    <div style={S}>
      <ModeBanner title="Morning Briefing — Scheduled" source="morning">
        Daily pre-market read across all instruments: lognormal <b>GBM move-probabilities</b>
        {" "}(±2%/±5%) blended with crew directional bias. Runs automatically at 10:00 AM ET.
      </ModeBanner>
      {/* Header */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16, marginTop: 6 }}>
        <div>
          <span style={{ fontFamily: "var(--font-display)", fontSize: 14, letterSpacing: ".2em", color: C.ink, textTransform: "uppercase" }}>
            Morning Briefing
          </span>
          {lastFetch && (
            <span style={{ fontSize: 10, color: C.dim, marginLeft: 10 }}>
              · updated {lastFetch}
            </span>
          )}
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          {/* Horizon toggle */}
          <div style={{ display: "flex", border: `1px solid ${C.lineHi}`, borderRadius: 3, overflow: "hidden" }}>
            {(["1d", "5d"] as const).map(h => (
              <button key={h} onClick={() => setHorizon(h)} style={toggleBtn(horizon === h)}>
                {h === "1d" ? "1D" : "5D"}
              </button>
            ))}
          </div>
          <button
            onClick={handleTrigger}
            disabled={triggering}
            style={{
              padding: "5px 12px", fontSize: 10, letterSpacing: ".1em", fontFamily: "var(--font-display)",
              fontWeight: 700, border: `1px solid ${C.amber}`, borderRadius: 3, cursor: "pointer",
              background: triggering ? "rgba(255,180,84,.15)" : "transparent", color: C.amber,
            }}
          >
            {triggering ? "RUNNING…" : "▶ RUN NOW"}
          </button>
          <button
            onClick={load}
            title="Refresh"
            style={{
              padding: "5px 9px", fontSize: 11,
              border: `1px solid ${C.lineHi}`, borderRadius: 3, cursor: "pointer",
              background: "transparent", color: C.dim,
            }}
          >
            ↻
          </button>
        </div>
      </div>

      {loading && !briefing && (
        <div style={{ color: C.dim, fontSize: 12, padding: "20px 0", textAlign: "center" }}>
          Loading briefing…
        </div>
      )}

      {!loading && !briefing && (
        <div style={{
          border: `1px dashed ${C.lineHi}`, borderRadius: 4,
          padding: 24, textAlign: "center", color: C.dim, fontSize: 12,
        }}>
          <div style={{ marginBottom: 8 }}>No daily briefing available yet.</div>
          <div style={{ fontSize: 11 }}>
            Press “▶ RUN NOW” to generate one manually, or it runs automatically at 10:00 AM ET on every trading day.
          </div>
        </div>
      )}

      {briefing && (
        <>
          <VixBanner briefing={briefing} horizon={horizon} />

          {/* Instrument grid */}
          <div style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(250px, 1fr))",
            gap: 14,
          }}>
            {briefing.instruments.map(inst => (
              <InstrumentCard key={inst.ticker} inst={inst} horizon={horizon} />
            ))}
          </div>

          {/* Footer */}
          <div style={{
            marginTop: 16, fontSize: 10, color: C.dim,
            borderTop: `1px solid ${C.line}`, paddingTop: 10, lineHeight: 1.5,
          }}>
            Probabilities are derived from a lognormal GBM model (historical vol + crew bias) — not a precise prediction.
            {" "}Engine: {briefing.engine_backend} · {briefing.instruments.filter(i => i.status === "done").length}/{briefing.instruments.length} runs completed.
          </div>
        </>
      )}
    </div>
  );
}
