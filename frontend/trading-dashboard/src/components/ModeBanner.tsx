/**
 * ModeBanner — declares, at the top of each workspace, exactly WHAT the page
 * shows and WHICH pipeline runs behind it. Makes the product story explicit:
 *
 *   LIVE DESK  → continuous monitoring from the fresh ingestion cache
 *   ANALYSIS   → on-demand deep-dive: live MCP data + chart vision + full crew
 *   BRIEFING   → scheduled morning GBM move-probabilities + crew bias
 *
 * The coloured "source chip" encodes the data provenance so a user (or a
 * recruiter watching the demo) instantly understands the difference between the
 * always-on cached views and the live, MCP-enriched deep-dive.
 */

export type SourceKind = "live" | "cached" | "morning";

const CHIP: Record<SourceKind, { label: string; color: string; glow: string }> = {
  live:    { label: "LIVE · MCP + VISION", color: "var(--bull)",  glow: "rgba(45,212,167,.35)" },
  cached:  { label: "CONTINUOUS · CACHED", color: "var(--amber)", glow: "rgba(255,180,84,.30)" },
  morning: { label: "MORNING · GBM MODEL", color: "var(--amber)", glow: "rgba(255,180,84,.30)" },
};

export function ModeBanner({
  title,
  source,
  children,
}: {
  title: string;
  source: SourceKind;
  children: React.ReactNode; // one-line description of the page's purpose / flow
}) {
  const chip = CHIP[source];
  return (
    <div className="mode-banner">
      <div className="mode-banner__main">
        <span className="mode-banner__title">{title}</span>
        <span className="mode-banner__desc">{children}</span>
      </div>
      <span
        className="mode-banner__chip mono"
        style={{ color: chip.color, borderColor: chip.color, boxShadow: `0 0 14px ${chip.glow}` }}
      >
        ◉ {chip.label}
      </span>
    </div>
  );
}
