/**
 * EvalDashboard — Phase 4 EVAL Research Lab UI
 *
 * Full-page view mounted at viewMode === "eval" in App.tsx.
 * Consumes GET /eval/summary from the agentic engine and renders:
 *
 *   ┌─────────────────────────────────────────────────────────┐
 *   │  HEADER BAR: title · refresh · swarm filter toggles    │
 *   ├──────────────────────┬──────────────────────────────────┤
 *   │  ScatterPlot         │  ConclusionsPanel                │
 *   │  cost vs quality     │  winner cards                    │
 *   ├──────────┬───────────┴──────────────────────────────────┤
 *   │ Bar:     │  Bar: Hallucination Rate by Swarm            │
 *   │ Faith.   │                                              │
 *   ├──────────┴───────────────────────────────────────────────┤
 *   │  TopRunsTable  (sortable)                               │
 *   └─────────────────────────────────────────────────────────┘
 *
 * Design system: inherits all CSS custom properties from styles.css.
 * No Tailwind. Recharts for charts. Native fetch — no swr/react-query.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  ScatterChart, Scatter, XAxis, YAxis, ZAxis,
  CartesianGrid, Tooltip, ResponsiveContainer, Legend,
  BarChart, Bar, Cell, LabelList,
} from "recharts";
// Static research artifact baked into the bundle at build time. Lets the EVAL
// dashboard render a complete, enriched dataset immediately — even when the
// agentic engine is offline — while the live /eval/summary fetch (below) still
// refreshes it when the engine is reachable.
import evalStaticData from "../data/evalSummary.json";

// ── Types (mirrors aggregate_eval_data.py output) ────────────────────────────

interface ConfigMetrics {
  config_id: string;
  swarm_size: "solo" | "triad" | "full";
  model_label: string;
  target_model: string | null;
  n_runs: number;
  local_ok: number;
  local_errors: number;
  error_rate: number;
  avg_cost_usd: number | null;
  total_cost_usd: number | null;
  avg_latency_lf_ms: number | null;
  avg_latency_local_s: number | null;
  avg_total_tokens: number | null;
  avg_faithfulness: number | null;
  avg_answer_relevancy: number | null;
  avg_schema_compliance: number | null;
  hallucination_rate: number | null;
  quality_score: number | null;
  cost_per_quality_unit: number | null;
  avg_bullish: number | null;
  avg_confidence: number | null;
}

interface ScatterPoint {
  config_id: string;
  swarm_size: string;
  model_label: string;
  cost_usd: number | null;
  quality_score: number | null;
  faithfulness: number | null;
  hallucination_rate: number | null;
  latency_ms: number | null;
  latency_local_s: number | null;
  avg_confidence: number | null;
  avg_bullish: number | null;
  n_runs: number;
}

interface ByGroup {
  model_label?: string;
  swarm_size?: string;
  avg_faithfulness: number | null;
  avg_hallucination: number | null;
  avg_cost_usd: number | null;
  avg_latency_lf_ms: number | null;
  avg_quality_score: number | null;
  total_runs: number;
}

interface ConclusionEntry {
  config_id: string;
  swarm_size: string;
  model_label: string;
  value?: number;
  metric?: string;
  quality_score?: number;
  avg_cost_usd?: number;
  rationale?: string;
}

interface Conclusions {
  best_quality?: ConclusionEntry;
  best_faithfulness?: ConclusionEntry;
  lowest_cost?: ConclusionEntry;
  lowest_latency?: ConclusionEntry;
  lowest_hallucination?: ConclusionEntry;
  best_cost_efficiency?: ConclusionEntry;
  best_balanced?: ConclusionEntry & { rationale: string };
}

interface TopRun {
  run_label: string;
  ticker: string;
  prompt_id: string;
  swarm_size: string;
  model_label: string;
  bullish: number | null;
  confidence: number | null;
  risk_level: string | null;
  latency_s: number | null;
  started_at: string;
}

interface Meta {
  total_local_runs: number;
  ok_runs: number;
  error_runs: number;
  unique_configs: number;
  langfuse_enriched_configs: number;
  phoenix_enriched_configs: number;
  aggregation_elapsed_s: number;
  jsonl_source: string;
}

interface EvalSummary {
  generated_at: string;
  experiment_name: string;
  meta: Meta;
  by_config: ConfigMetrics[];
  by_model: ByGroup[];
  by_swarm: ByGroup[];
  scatter_data: ScatterPoint[];
  top_runs: TopRun[];
  conclusions: Conclusions;
}

// ── Design tokens ─────────────────────────────────────────────────────────────

const T = {
  bg:        "var(--bg)",
  bgRaise:   "var(--bg-raise)",
  bgCard:    "var(--bg-card)",
  line:      "var(--line)",
  lineBright:"var(--line-bright)",
  ink:       "var(--ink)",
  inkDim:    "var(--ink-dim)",
  amber:     "var(--amber)",
  bull:      "var(--bull)",
  bear:      "var(--bear)",
  flat:      "var(--flat)",
};

const SWARM_COLORS: Record<string, string> = {
  solo:  "#a78bfa",   // violet — cheap, single-agent
  triad: "#38bdf8",   // sky blue — focused derivatives triad
  full:  "#2dd4a7",   // neo-mint (matches --bull) — full desk
};

const MODEL_COLORS: Record<string, string> = {
  "gemini-2.5-flash": "#fbbf24",
  "llama-3.3-70b":    "#f87171",
  "gpt-4o":           "#34d399",
};

const SWARM_ORDER = ["solo", "triad", "full"];

// Shared height for the Row-1 cards (scatter + conclusions) so the tall
// conclusions list can't stretch the row and leave a hole under the scatter.
const ROW1_HEIGHT = 440;

// ── API fetch ─────────────────────────────────────────────────────────────────

const AGENTIC_URL = (import.meta as any).env?.VITE_AGENTIC_URL ?? "http://localhost:8003";

async function fetchEvalSummary(noLangfuse = false, noPhoenix = false): Promise<EvalSummary> {
  const params = new URLSearchParams();
  if (noLangfuse) params.set("no_langfuse", "true");
  if (noPhoenix)  params.set("no_phoenix",  "true");
  const qs = params.toString() ? `?${params}` : "";
  const res = await fetch(`${AGENTIC_URL}/eval/summary${qs}`);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? `HTTP ${res.status}`);
  }
  return res.json();
}

// ── Sub-components ────────────────────────────────────────────────────────────

/** Translucent card matching the existing QST card aesthetic. */
function Card({
  title, children, style,
}: { title?: string; children: React.ReactNode; style?: React.CSSProperties }) {
  return (
    <div style={{
      background: T.bgCard,
      border: `1px solid ${T.lineBright}`,
      borderRadius: 6,
      padding: "18px 20px",
      ...style,
    }}>
      {title && (
        <div style={{
          fontFamily: "var(--font-display)",
          fontSize: 11,
          letterSpacing: 2,
          color: T.amber,
          textTransform: "uppercase",
          marginBottom: 14,
        }}>
          {title}
        </div>
      )}
      {children}
    </div>
  );
}

/** Glowing pill badge for swarm size labels. */
function SwarmBadge({ swarm }: { swarm: string }) {
  const color = SWARM_COLORS[swarm] ?? T.flat;
  return (
    <span style={{
      display: "inline-block",
      padding: "1px 8px",
      borderRadius: 3,
      fontSize: 10,
      fontFamily: "var(--font-mono)",
      fontWeight: 700,
      letterSpacing: 1,
      color,
      border: `1px solid ${color}44`,
      background: `${color}18`,
      textTransform: "uppercase",
    }}>
      {swarm}
    </span>
  );
}

/** Metric stat tile used in conclusion cards. */
function Stat({
  label, value, accent,
}: { label: string; value: string | number | null; accent?: string }) {
  return (
    <div style={{ marginTop: 4 }}>
      <span style={{ color: T.inkDim, fontSize: 10, fontFamily: "var(--font-mono)", letterSpacing: 1, textTransform: "uppercase" }}>
        {label}&ensp;
      </span>
      <span style={{ color: accent ?? T.ink, fontWeight: 700, fontFamily: "var(--font-mono)", fontSize: 13 }}>
        {value ?? "—"}
      </span>
    </div>
  );
}

// ── Scatter Plot ──────────────────────────────────────────────────────────────

interface CustomDotProps {
  cx?: number; cy?: number; payload?: ScatterPoint;
}

function CustomDot({ cx = 0, cy = 0, payload }: CustomDotProps) {
  if (!payload) return null;
  const color = SWARM_COLORS[payload.swarm_size] ?? T.flat;
  return (
    <g>
      <circle
        cx={cx} cy={cy} r={9}
        fill={color}
        fillOpacity={0.18}
        stroke={color}
        strokeWidth={1.5}
      />
      <circle cx={cx} cy={cy} r={3.5} fill={color} />
    </g>
  );
}

interface ScatterTooltipProps {
  active?: boolean; payload?: Array<{ payload: ScatterPoint }>;
}

function ScatterTooltip({ active, payload }: ScatterTooltipProps) {
  if (!active || !payload?.length) return null;
  const d = payload[0].payload;
  const latLabel = d.latency_local_s != null
    ? `${d.latency_local_s.toFixed(1)}s (local)`
    : d.latency_ms != null ? `${(d.latency_ms / 1000).toFixed(1)}s (lf)` : "—";

  return (
    <div style={{
      background: T.bgRaise,
      border: `1px solid ${T.lineBright}`,
      borderRadius: 5,
      padding: "10px 14px",
      fontFamily: "var(--font-mono)",
      fontSize: 11,
      minWidth: 200,
    }}>
      <div style={{ color: SWARM_COLORS[d.swarm_size] ?? T.amber, fontWeight: 700, marginBottom: 6 }}>
        {d.config_id}
      </div>
      <div style={{ color: T.ink }}>Model: <b>{d.model_label}</b></div>
      <SwarmBadge swarm={d.swarm_size} />
      <div style={{ marginTop: 6, borderTop: `1px solid ${T.line}`, paddingTop: 6 }}>
        <div>Cost/run:  <b style={{ color: T.amber }}>{d.cost_usd != null ? `$${d.cost_usd.toFixed(5)}` : "no Langfuse data"}</b></div>
        <div>Quality:   <b style={{ color: T.bull }}>{d.quality_score != null ? d.quality_score.toFixed(4) : "no eval data"}</b></div>
        <div>Latency:   {latLabel}</div>
        <div>Confidence: {d.avg_confidence != null ? d.avg_confidence.toFixed(3) : "—"}</div>
        <div>n={d.n_runs} runs</div>
      </div>
    </div>
  );
}

function ScatterPlot({
  data,
  visibleSwarms,
}: { data: ScatterPoint[]; visibleSwarms: Set<string> }) {
  const filtered = data.filter(d => visibleSwarms.has(d.swarm_size));

  // When cost is null (offline mode), fall back to latency_local_s on X
  const hasRealCost = filtered.some(d => d.cost_usd != null);
  const hasQuality  = filtered.some(d => d.quality_score != null);

  // Group points by swarm size for separate Scatter series (distinct legend colours)
  const bySwarm = SWARM_ORDER
    .filter(sw => visibleSwarms.has(sw))
    .map(sw => ({
      swarm: sw,
      points: filtered
        .filter(d => 
          d.swarm_size === sw && 
          (hasRealCost ? d.cost_usd != null : d.latency_local_s != null) &&
          (hasQuality ? d.quality_score != null : d.avg_confidence != null)
        )
        .map(d => ({
          ...d,
          x: hasRealCost ? d.cost_usd! : d.latency_local_s!,
          y: hasQuality  ? d.quality_score! : d.avg_confidence!,
        })),
    }));

  const xLabel = hasRealCost ? "Cost / Run (USD)" : "Latency (s) — cost data pending Langfuse";
  const yLabel = hasQuality  ? "Quality Score"    : "Avg Confidence — eval data pending";

  return (
    <Card title="⬡ Cost vs Quality — Swarm Impact Scatter" style={{ height: ROW1_HEIGHT, display: "flex", flexDirection: "column" }}>
      <div style={{ marginBottom: 10, fontSize: 11, color: T.inkDim, fontFamily: "var(--font-mono)" }}>
        {!hasRealCost && (
          <span style={{ color: T.amber }}>
            ⚠ Langfuse cost data not yet available — showing local latency on X axis.
            Run the aggregation with Langfuse connected to unlock the full scatter.
          </span>
        )}
      </div>
      <div style={{ flex: 1, minHeight: 0 }}>
      <ResponsiveContainer width="100%" height="100%">
        <ScatterChart margin={{ top: 10, right: 30, bottom: 30, left: 10 }}>
          <CartesianGrid stroke={T.line} strokeDasharray="3 3" />
          <XAxis
            dataKey="x"
            type="number"
            name={xLabel}
            tickFormatter={v => hasRealCost ? `$${Number(v).toFixed(4)}` : `${Number(v).toFixed(1)}s`}
            tick={{ fill: T.inkDim, fontFamily: "var(--font-mono)", fontSize: 10 }}
            label={{ value: xLabel, position: "insideBottom", offset: -10, fill: T.inkDim, fontSize: 10 }}
          />
          <YAxis
            dataKey="y"
            type="number"
            name={yLabel}
            domain={[0, 1]}
            tickFormatter={v => Number(v).toFixed(2)}
            tick={{ fill: T.inkDim, fontFamily: "var(--font-mono)", fontSize: 10 }}
            label={{ value: yLabel, angle: -90, position: "insideLeft", fill: T.inkDim, fontSize: 10 }}
          />
          <ZAxis range={[80, 80]} />
          <Tooltip content={<ScatterTooltip />} />
          <Legend
            wrapperStyle={{ fontFamily: "var(--font-mono)", fontSize: 11, paddingTop: 8 }}
            formatter={(value) => (
              <span style={{ color: SWARM_COLORS[value] ?? T.ink }}>{value.toUpperCase()}</span>
            )}
          />
          {bySwarm.map(({ swarm, points }) => (
            <Scatter
              key={swarm}
              name={swarm}
              data={points}
              fill={SWARM_COLORS[swarm]}
              shape={<CustomDot />}
            />
          ))}
        </ScatterChart>
      </ResponsiveContainer>
      </div>
    </Card>
  );
}

// ── Bar Charts ────────────────────────────────────────────────────────────────

function FaithfulnessBarChart({ byModel }: { byModel: ByGroup[] }) {
  const data = byModel.map(m => ({
    name: m.model_label ?? "?",
    faithfulness: m.avg_faithfulness != null ? +(m.avg_faithfulness * 100).toFixed(1) : null,
    relevancy:    null as number | null, // placeholder for future
  }));

  return (
    <Card title="◈ Faithfulness Score by Model">
      {data.every(d => d.faithfulness == null) ? (
        <div style={{ color: T.inkDim, fontFamily: "var(--font-mono)", fontSize: 11, padding: "40px 0", textAlign: "center" }}>
          Faithfulness scores pending — connect Langfuse and re-aggregate.
        </div>
      ) : (
        <ResponsiveContainer width="100%" height={220}>
          <BarChart data={data} margin={{ top: 10, right: 16, left: 0, bottom: 0 }}>
            <CartesianGrid stroke={T.line} strokeDasharray="3 3" vertical={false} />
            <XAxis
              dataKey="name"
              tick={{ fill: T.inkDim, fontFamily: "var(--font-mono)", fontSize: 10 }}
            />
            <YAxis
              domain={[0, 100]}
              tickFormatter={v => `${v}%`}
              tick={{ fill: T.inkDim, fontFamily: "var(--font-mono)", fontSize: 10 }}
            />
            <Tooltip
              formatter={(v) => [`${v}%`, "Faithfulness"]}
              contentStyle={{ background: T.bgRaise, border: `1px solid ${T.lineBright}`, fontFamily: "var(--font-mono)", fontSize: 11 }}
              labelStyle={{ color: T.amber }}
            />
            <Bar dataKey="faithfulness" radius={[3, 3, 0, 0]} maxBarSize={54}>
              {data.map((d, i) => (
                <Cell key={i} fill={MODEL_COLORS[d.name] ?? T.flat} />
              ))}
              <LabelList
                dataKey="faithfulness"
                position="top"
                formatter={(v: any) => v != null ? `${v}%` : ""}
                style={{ fontFamily: "var(--font-mono)", fontSize: 10, fill: T.ink }}
              />
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      )}
    </Card>
  );
}

function HallucinationBarChart({ bySwarm }: { bySwarm: ByGroup[] }) {
  const ordered = SWARM_ORDER
    .map(sw => bySwarm.find(b => b.swarm_size === sw))
    .filter(Boolean) as ByGroup[];

  const data = ordered.map(s => ({
    name: s.swarm_size?.toUpperCase() ?? "?",
    swarm: s.swarm_size ?? "",
    rate: s.avg_hallucination != null ? +(s.avg_hallucination * 100).toFixed(1) : null,
  }));

  const hasAnyData = data.some(d => d.rate != null);
  // A genuine 0% across all scored swarms is a *good* result, not missing data —
  // recharts would draw invisible zero-height bars, so show it as a clear state.
  const allClean = hasAnyData && data.every(d => d.rate == null || d.rate === 0);

  return (
    <Card title="⚠ Hallucination Rate by Swarm Size">
      {!hasAnyData ? (
        <div style={{ color: T.inkDim, fontFamily: "var(--font-mono)", fontSize: 11, padding: "40px 0", textAlign: "center" }}>
          Hallucination data pending eval scores — connect Langfuse/Phoenix and re-aggregate.
        </div>
      ) : allClean ? (
        <div style={{
          height: 220, display: "flex", flexDirection: "column",
          alignItems: "center", justifyContent: "center", gap: 12,
        }}>
          <div style={{ fontSize: 38, lineHeight: 1, color: T.bull }}>✓</div>
          <div style={{ fontFamily: "var(--font-display)", fontSize: 16, letterSpacing: 1.5, color: T.bull }}>
            0% HALLUCINATION RATE
          </div>
          <div style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: T.inkDim, textAlign: "center", maxWidth: 340, lineHeight: 1.6 }}>
            Every scored run passed schema-compliance — no ungrounded or malformed
            outputs detected across the evaluated swarms.
          </div>
          <div style={{ display: "flex", gap: 8, marginTop: 2 }}>
            {data.map(d => (
              <span key={d.name} style={{
                fontFamily: "var(--font-mono)", fontSize: 10, fontWeight: 700, letterSpacing: 1,
                padding: "2px 9px", borderRadius: 3,
                color: d.rate != null ? T.bull : T.inkDim,
                border: `1px solid ${d.rate != null ? T.bull : T.line}44`,
                background: d.rate != null ? `${T.bull}14` : "transparent",
              }}>
                {d.name} {d.rate != null ? `${d.rate}%` : "—"}
              </span>
            ))}
          </div>
        </div>
      ) : (
        <ResponsiveContainer width="100%" height={220}>
          <BarChart data={data} margin={{ top: 10, right: 16, left: 0, bottom: 0 }}>
            <CartesianGrid stroke={T.line} strokeDasharray="3 3" vertical={false} />
            <XAxis
              dataKey="name"
              tick={{ fill: T.inkDim, fontFamily: "var(--font-mono)", fontSize: 10 }}
            />
            <YAxis
              domain={[0, 100]}
              tickFormatter={v => `${v}%`}
              tick={{ fill: T.inkDim, fontFamily: "var(--font-mono)", fontSize: 10 }}
            />
            <Tooltip
              formatter={(v) => [`${v}%`, "Hallucination Rate"]}
              contentStyle={{ background: T.bgRaise, border: `1px solid ${T.lineBright}`, fontFamily: "var(--font-mono)", fontSize: 11 }}
              labelStyle={{ color: T.amber }}
            />
            <Bar dataKey="rate" radius={[3, 3, 0, 0]} maxBarSize={54}>
              {data.map((d, i) => (
                <Cell
                  key={i}
                  fill={d.rate != null && d.rate > 15 ? T.bear : SWARM_COLORS[d.swarm] ?? T.flat}
                />
              ))}
              <LabelList
                dataKey="rate"
                position="top"
                formatter={(v: any) => v != null ? `${v}%` : ""}
                style={{ fontFamily: "var(--font-mono)", fontSize: 10, fill: T.ink }}
              />
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      )}
    </Card>
  );
}

// ── Conclusions Panel ─────────────────────────────────────────────────────────

const CONCLUSION_META: Record<string, { label: string; icon: string; accent: string; desc: string }> = {
  best_balanced:        { label: "Best Balanced",      icon: "⚖",  accent: T.bull,  desc: "Best quality-to-cost ratio" },
  best_quality:         { label: "Best Quality",        icon: "◎",  accent: "#a78bfa", desc: "Highest composite quality score" },
  best_faithfulness:    { label: "Most Faithful",       icon: "◈",  accent: "#38bdf8", desc: "Highest faithfulness score" },
  lowest_cost:          { label: "Cheapest",            icon: "◇",  accent: T.amber, desc: "Lowest avg cost per run" },
  lowest_hallucination: { label: "Most Grounded",       icon: "✓",  accent: T.bull,  desc: "Lowest hallucination rate" },
  lowest_latency:       { label: "Fastest",             icon: "⚡", accent: "#fcd34d", desc: "Lowest end-to-end latency" },
  best_cost_efficiency: { label: "Best $/Quality",      icon: "⬡",  accent: "#fb923c", desc: "Best cost-per-quality-unit" },
};

function ConclusionCard({
  cKey,
  entry,
}: { cKey: string; entry: ConclusionEntry & { rationale?: string } }) {
  const meta = CONCLUSION_META[cKey] ?? { label: cKey, icon: "▸", accent: T.amber, desc: "" };
  const valueStr = (() => {
    if (entry.value != null) {
      if (cKey === "lowest_cost" || cKey === "best_cost_efficiency")
        return `$${Number(entry.value).toFixed(6)}`;
      if (cKey === "lowest_latency")
        return `${Number(entry.value).toFixed(0)} ms`;
      return Number(entry.value).toFixed(4);
    }
    if (entry.quality_score != null) return Number(entry.quality_score).toFixed(4);
    return null;
  })();

  return (
    <div style={{
      background: T.bgRaise,
      border: `1px solid ${meta.accent}2a`,
      borderLeft: `3px solid ${meta.accent}`,
      borderRadius: 5,
      padding: "12px 14px",
      position: "relative",
      overflow: "hidden",
    }}>
      {/* Subtle glow accent */}
      <div style={{
        position: "absolute", top: 0, right: 0, width: 80, height: "100%",
        background: `radial-gradient(ellipse at right, ${meta.accent}0c, transparent 70%)`,
        pointerEvents: "none",
      }} />

      <div style={{ fontSize: 10, letterSpacing: 2, color: meta.accent, fontFamily: "var(--font-mono)", textTransform: "uppercase", marginBottom: 4 }}>
        {meta.icon} {meta.label}
      </div>
      <div style={{ fontSize: 13, fontWeight: 700, color: T.ink, fontFamily: "var(--font-mono)", marginBottom: 3 }}>
        {entry.config_id}
      </div>
      <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap", marginBottom: 5 }}>
        <SwarmBadge swarm={entry.swarm_size} />
        <span style={{ fontSize: 10, color: MODEL_COLORS[entry.model_label] ?? T.flat, fontFamily: "var(--font-mono)" }}>
          {entry.model_label}
        </span>
      </div>
      {valueStr != null && (
        <Stat label={entry.metric ?? "score"} value={valueStr} accent={meta.accent} />
      )}
      {entry.rationale && (
        <div style={{ fontSize: 10, color: T.inkDim, marginTop: 6, lineHeight: 1.5, fontFamily: "var(--font-mono)" }}>
          {entry.rationale}
        </div>
      )}
    </div>
  );
}

function ConclusionsPanel({ conclusions }: { conclusions: Conclusions }) {
  const entries = Object.entries(conclusions).filter(([, v]) => v != null) as [string, ConclusionEntry][];

  if (entries.length === 0) {
    return (
      <Card title="▸ Conclusions — Research Findings" style={{ height: "100%" }}>
        <div style={{ color: T.inkDim, fontSize: 11, fontFamily: "var(--font-mono)", padding: "40px 0", textAlign: "center", lineHeight: 1.8 }}>
          Conclusions will appear once Langfuse and Phoenix data is integrated.<br />
          In offline mode, run the aggregation with live observability endpoints.
        </div>
      </Card>
    );
  }

  // Always show best_balanced first — that's the headline finding
  const ordered = [
    ...entries.filter(([k]) => k === "best_balanced"),
    ...entries.filter(([k]) => k !== "best_balanced"),
  ];

  return (
    <Card title="▸ Conclusions — Research Findings" style={{ height: ROW1_HEIGHT, display: "flex", flexDirection: "column" }}>
      <div style={{
        display: "flex", flexDirection: "column", gap: 10,
        flex: 1, minHeight: 0, overflowY: "auto", paddingRight: 4,
      }}>
        {ordered.map(([k, v]) => (
          <ConclusionCard key={k} cKey={k} entry={v} />
        ))}
      </div>
    </Card>
  );
}

// ── Top Runs Table ────────────────────────────────────────────────────────────

type SortKey = keyof TopRun;

function TopRunsTable({ runs }: { runs: TopRun[] }) {
  const [sortKey, setSortKey]   = useState<SortKey>("confidence");
  const [sortAsc, setSortAsc]   = useState(false);
  const [filterText, setFilter] = useState("");

  const sorted = [...runs]
    .filter(r => !filterText || JSON.stringify(r).toLowerCase().includes(filterText.toLowerCase()))
    .sort((a, b) => {
      const av = a[sortKey], bv = b[sortKey];
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      const cmp = av < bv ? -1 : av > bv ? 1 : 0;
      return sortAsc ? cmp : -cmp;
    });

  const col = (key: SortKey, label: string, w?: number) => (
    <th
      onClick={() => { setSortKey(key); setSortAsc(s => sortKey === key ? !s : false); }}
      style={{
        cursor: "pointer",
        fontFamily: "var(--font-mono)",
        fontSize: 9,
        letterSpacing: 1.5,
        color: sortKey === key ? T.amber : T.inkDim,
        textAlign: "left",
        padding: "6px 10px",
        borderBottom: `1px solid ${T.lineBright}`,
        whiteSpace: "nowrap",
        width: w,
        userSelect: "none",
      }}
    >
      {label}{sortKey === key ? (sortAsc ? " ▲" : " ▼") : ""}
    </th>
  );

  const riskColor = (r: string | null) =>
    r === "HIGH" ? T.bear : r === "LOW" ? T.bull : T.flat;

  return (
    <Card title={`◎ Top ${sorted.length} Runs — Drill-Down`}>
      <input
        type="text"
        placeholder="Filter by ticker, model, swarm, prompt…"
        value={filterText}
        onChange={e => setFilter(e.target.value)}
        style={{
          width: "100%",
          background: T.bgRaise,
          border: `1px solid ${T.lineBright}`,
          borderRadius: 4,
          color: T.ink,
          fontFamily: "var(--font-mono)",
          fontSize: 11,
          padding: "7px 10px",
          marginBottom: 12,
          outline: "none",
        }}
      />
      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11 }}>
          <thead>
            <tr>
              {col("ticker",     "TICKER",    64)}
              {col("swarm_size", "SWARM",     60)}
              {col("model_label","MODEL",    160)}
              {col("prompt_id",  "PROMPT",   180)}
              {col("confidence", "CONF",      60)}
              {col("bullish",    "BULL%",     60)}
              {col("risk_level", "RISK",      60)}
              {col("latency_s",  "LAT (s)",   70)}
            </tr>
          </thead>
          <tbody>
            {sorted.map((r, i) => (
              <tr
                key={i}
                style={{
                  borderBottom: `1px solid ${T.line}`,
                  background: i % 2 === 0 ? "transparent" : `${T.bgRaise}88`,
                  transition: "background 0.12s",
                }}
                onMouseEnter={e => (e.currentTarget.style.background = `${T.lineBright}44`)}
                onMouseLeave={e => (e.currentTarget.style.background = i % 2 === 0 ? "transparent" : `${T.bgRaise}88`)}
              >
                <td style={{ padding: "5px 10px", fontFamily: "var(--font-mono)", fontWeight: 700, color: T.amber }}>
                  {r.ticker}
                </td>
                <td style={{ padding: "5px 10px" }}>
                  <SwarmBadge swarm={r.swarm_size} />
                </td>
                <td style={{ padding: "5px 10px", fontFamily: "var(--font-mono)", fontSize: 10, color: MODEL_COLORS[r.model_label] ?? T.flat }}>
                  {r.model_label}
                </td>
                <td style={{ padding: "5px 10px", fontFamily: "var(--font-mono)", fontSize: 10, color: T.inkDim }}>
                  {r.prompt_id}
                </td>
                <td style={{ padding: "5px 10px", fontFamily: "var(--font-mono)", fontWeight: 700 }}>
                  {r.confidence != null ? r.confidence.toFixed(3) : "—"}
                </td>
                <td style={{ padding: "5px 10px", fontFamily: "var(--font-mono)", color: T.bull }}>
                  {r.bullish != null ? `${(r.bullish * 100).toFixed(0)}%` : "—"}
                </td>
                <td style={{ padding: "5px 10px", fontFamily: "var(--font-mono)", fontWeight: 700, color: riskColor(r.risk_level) }}>
                  {r.risk_level ?? "—"}
                </td>
                <td style={{ padding: "5px 10px", fontFamily: "var(--font-mono)", color: T.inkDim }}>
                  {r.latency_s != null ? r.latency_s.toFixed(1) : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

// ── Meta Bar ─────────────────────────────────────────────────────────────────

function MetaBar({ meta, generatedAt }: { meta: Meta; generatedAt: string }) {
  const ts = new Date(generatedAt).toLocaleTimeString();
  const src = meta.jsonl_source?.split(/[\\/]/).pop() ?? "—";
  return (
    <div style={{
      display: "flex",
      gap: 20,
      flexWrap: "wrap",
      alignItems: "center",
      fontFamily: "var(--font-mono)",
      fontSize: 10,
      color: T.inkDim,
      padding: "8px 0",
      borderBottom: `1px solid ${T.line}`,
      marginBottom: 18,
    }}>
      <span style={{ color: T.amber }}>⬡ EVAL RESEARCH LAB</span>
      <span>Runs: <b style={{ color: T.ink }}>{meta.ok_runs}/{meta.total_local_runs}</b></span>
      <span>Configs: <b style={{ color: T.ink }}>{meta.unique_configs}</b></span>
      <span>Langfuse enriched: <b style={{ color: meta.langfuse_enriched_configs > 0 ? T.bull : T.bear }}>{meta.langfuse_enriched_configs}</b></span>
      <span>Phoenix enriched: <b style={{ color: meta.phoenix_enriched_configs > 0 ? T.bull : T.bear }}>{meta.phoenix_enriched_configs}</b></span>
      <span>Source: <i>{src}</i></span>
      <span style={{ marginLeft: "auto" }}>Last updated: {ts} · {meta.aggregation_elapsed_s}s</span>
    </div>
  );
}

// ── Main EvalDashboard ────────────────────────────────────────────────────────

export function EvalDashboard() {
  const [data, setData]           = useState<EvalSummary | null>(evalStaticData as unknown as EvalSummary);
  const [loading, setLoading]     = useState(false);
  const [error, setError]         = useState<string | null>(null);
  const [visibleSwarms, setVisible] = useState<Set<string>>(new Set(SWARM_ORDER));
  const [offlineMode, setOffline] = useState(false);
  const lastFetchRef              = useRef<number>(0);

  const load = useCallback(async () => {
    if (loading) return;
    setLoading(true);
    setError(null);
    try {
      const d = await fetchEvalSummary(offlineMode, offlineMode);
      setData(d);
      lastFetchRef.current = Date.now();
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, [loading, offlineMode]);

  // Load once on mount
  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const toggleSwarm = (sw: string) =>
    setVisible(prev => {
      const next = new Set(prev);
      next.has(sw) ? next.delete(sw) : next.add(sw);
      return next;
    });

  const headerH = (
    <div style={{
      display: "flex",
      alignItems: "center",
      gap: 12,
      flexWrap: "wrap",
      marginBottom: 18,
    }}>
      <div>
        <h2 style={{
          fontFamily: "var(--font-display)",
          fontSize: 22,
          color: T.amber,
          margin: 0,
          letterSpacing: 2,
        }}>
          SWARM RESEARCH — EVAL DASHBOARD
        </h2>
        <p style={{ margin: 0, color: T.inkDim, fontSize: 11, fontFamily: "var(--font-mono)" }}>
          Phase 4 · Swarm Size vs Model Impact · VIX/Derivatives Benchmark
        </p>
      </div>

      <div style={{ marginLeft: "auto", display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        {/* Swarm filter toggles */}
        {SWARM_ORDER.map(sw => (
          <button
            key={sw}
            onClick={() => toggleSwarm(sw)}
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 10,
              letterSpacing: 1,
              textTransform: "uppercase",
              padding: "4px 10px",
              borderRadius: 4,
              border: `1px solid ${SWARM_COLORS[sw]}`,
              background: visibleSwarms.has(sw) ? `${SWARM_COLORS[sw]}25` : "transparent",
              color: visibleSwarms.has(sw) ? SWARM_COLORS[sw] : T.inkDim,
              cursor: "pointer",
              transition: "all 0.15s",
            }}
          >
            {visibleSwarms.has(sw) ? "◉" : "○"} {sw}
          </button>
        ))}

        {/* Offline mode toggle */}
        <button
          onClick={() => setOffline(o => !o)}
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 10,
            letterSpacing: 1,
            textTransform: "uppercase",
            padding: "4px 10px",
            borderRadius: 4,
            border: `1px solid ${T.lineBright}`,
            background: offlineMode ? `${T.amber}15` : "transparent",
            color: offlineMode ? T.amber : T.inkDim,
            cursor: "pointer",
          }}
          title="When enabled, skips Langfuse/Phoenix API calls — instant response from local JSONL only"
        >
          {offlineMode ? "◉ OFFLINE" : "○ OFFLINE"}
        </button>

        {/* Refresh button */}
        <button
          onClick={load}
          disabled={loading}
          className="refresh-btn mono"
          style={{ fontSize: 11 }}
        >
          {loading ? "LOADING…" : "⟳ REFRESH"}
        </button>
      </div>
    </div>
  );

  if (error && !data) {
    return (
      <div style={{ padding: "32px 0" }}>
        {headerH}
        <div style={{
          background: `${T.bear}12`,
          border: `1px solid ${T.bear}44`,
          borderRadius: 6,
          padding: "24px 28px",
          fontFamily: "var(--font-mono)",
          color: T.bear,
        }}>
          <div style={{ fontWeight: 700, marginBottom: 8 }}>✕ Aggregation Error</div>
          <div style={{ fontSize: 12 }}>{error}</div>
          <div style={{ marginTop: 12, color: T.inkDim, fontSize: 11 }}>
            Make sure the agentic engine is running on port 8003 and has benchmark data
            (run <code>python scripts/run_eval_matrix.py</code> first).
          </div>
          <button
            onClick={load}
            style={{
              marginTop: 14,
              fontFamily: "var(--font-mono)",
              fontSize: 11,
              padding: "6px 14px",
              borderRadius: 4,
              border: `1px solid ${T.bear}`,
              background: "transparent",
              color: T.bear,
              cursor: "pointer",
            }}
          >
            ⟳ RETRY
          </button>
        </div>
      </div>
    );
  }

  if (!data && loading) {
    return (
      <div style={{ padding: "32px 0" }}>
        {headerH}
        <div style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          height: 320,
          fontFamily: "var(--font-mono)",
          color: T.inkDim,
          fontSize: 13,
          flexDirection: "column",
          gap: 12,
        }}>
          <div className="mono" style={{ color: T.amber, fontSize: 18, animation: "spin 1s linear infinite" }}>⟳</div>
          Aggregating benchmark data…
          <div style={{ fontSize: 10 }}>Fetching from Langfuse + Phoenix + local JSONL</div>
        </div>
      </div>
    );
  }

  if (!data) return null;

  return (
    <div style={{ padding: "24px 0" }}>
      {headerH}
      <MetaBar meta={data.meta} generatedAt={data.generated_at} />

      {/* Row 1: Scatter + Conclusions */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 380px", gap: 16, marginBottom: 16 }}>
        <ScatterPlot data={data.scatter_data} visibleSwarms={visibleSwarms} />
        <ConclusionsPanel conclusions={data.conclusions} />
      </div>

      {/* Row 2: Bar charts side by side */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, marginBottom: 16 }}>
        <FaithfulnessBarChart byModel={data.by_model} />
        <HallucinationBarChart bySwarm={data.by_swarm} />
      </div>

      {/* Row 3: Full-width table */}
      <TopRunsTable runs={data.top_runs} />

      {/* Footer data source note */}
      <div style={{
        marginTop: 14,
        fontFamily: "var(--font-mono)",
        fontSize: 9,
        color: T.inkDim,
        textAlign: "center",
        letterSpacing: 1,
      }}>
        DATA SOURCE: {data.meta.jsonl_source} ·
        LANGFUSE ENRICHED: {data.meta.langfuse_enriched_configs}/{data.meta.unique_configs} CONFIGS ·
        PHOENIX ENRICHED: {data.meta.phoenix_enriched_configs}/{data.meta.unique_configs} CONFIGS
      </div>
    </div>
  );
}
