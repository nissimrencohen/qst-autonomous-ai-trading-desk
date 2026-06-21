import type { MarketLive, TickerSignal, VixData } from "../types";

interface Props {
  market: MarketLive | null;
  loading: boolean;
  error: string | null;
  onRunAnalysis: (ticker: string) => void;
}

const PORTFOLIO_LABELS: Record<string, string> = {
  "10000":   "$10K",
  "50000":   "$50K",
  "100000":  "$100K",
  "500000":  "$500K",
  "1000000": "$1M",
};

const REGIME_COLOR: Record<string, string> = {
  calm:     "var(--bull)",
  elevated: "var(--amber)",
  stress:   "#ff9500",
  panic:    "var(--bear)",
};

const HEAT_COLOR: Record<string, string> = {
  low:     "var(--bull)",
  medium:  "var(--amber)",
  high:    "#ff9500",
  extreme: "var(--bear)",
};

function fmt(n: number, dec = 2) {
  return n.toLocaleString("en-US", { minimumFractionDigits: dec, maximumFractionDigits: dec });
}

function SignalBadge({ signal, strength }: { signal: string; strength: number }) {
  const color =
    signal === "bullish" ? "var(--bull)" :
    signal === "bearish" ? "var(--bear)" : "var(--amber)";
  const label = signal.toUpperCase();
  const pct = Math.round(strength * 100);
  return (
    <div className="cc-signal">
      <span className="cc-signal__label" style={{ color }}>{label}</span>
      <div className="cc-signal__bar-track">
        <div
          className="cc-signal__bar-fill"
          style={{ width: `${pct}%`, background: color, boxShadow: `0 0 8px ${color}` }}
        />
      </div>
      <span className="cc-signal__pct mono" style={{ color }}>{pct}%</span>
    </div>
  );
}

function TickerDesk({ sym, data, onAnalyze }: {
  sym: string;
  data: TickerSignal;
  onAnalyze: () => void;
}) {
  const isBull = data.signal === "bullish";
  const isBear = data.signal === "bearish";
  const accentColor = isBull ? "var(--bull)" : isBear ? "var(--bear)" : "var(--amber)";
  const changeColor = data.change_pct >= 0 ? "var(--bull)" : "var(--bear)";

  return (
    <div className="cc-desk" style={{ borderTopColor: accentColor }}>
      {/* Header */}
      <div className="cc-desk__head">
        <div>
          <span className="cc-desk__sym">{sym}</span>
          <span className="cc-desk__name">{data.name}</span>
        </div>
        <button className="cc-desk__analyze" onClick={onAnalyze}>AI ANALYSIS →</button>
      </div>

      {/* Price */}
      <div className="cc-desk__price">
        <span className="cc-desk__price-val">${fmt(data.price)}</span>
        <span className="cc-desk__chg mono" style={{ color: changeColor }}>
          {data.change_pct >= 0 ? "▲" : "▼"} {Math.abs(data.change_pct).toFixed(2)}%
        </span>
      </div>

      {/* Signal bar */}
      <SignalBadge signal={data.signal} strength={data.strength} />

      {/* Key levels */}
      <div className="cc-levels">
        <div className="cc-level">
          <span className="cc-level__lbl">ENTRY ZONE</span>
          <span className="cc-level__val cc-level--entry">
            ${fmt(data.entry_zone[0])} – ${fmt(data.entry_zone[1])}
          </span>
        </div>
        <div className="cc-level">
          <span className="cc-level__lbl">TARGET</span>
          <span className="cc-level__val" style={{ color: "var(--bull)" }}>
            ${fmt(data.target)}
          </span>
        </div>
        <div className="cc-level">
          <span className="cc-level__lbl">STOP LOSS</span>
          <span className="cc-level__val" style={{ color: "var(--bear)" }}>
            ${fmt(data.stop)}
          </span>
        </div>
        <div className="cc-level">
          <span className="cc-level__lbl">RISK/REWARD</span>
          <span className="cc-level__val" style={{ color: "var(--amber)" }}>
            {fmt(data.risk_reward, 1)}:1
          </span>
        </div>
      </div>

      {/* MA context */}
      <div className="cc-mas mono">
        <span>MA20 <b>${fmt(data.ma20)}</b></span>
        <span>MA50 <b>${fmt(data.ma50)}</b></span>
        <span>ATR  <b>${fmt(data.atr)}</b></span>
      </div>

      {/* 52W Range */}
      {data["52w_high"] && data["52w_low"] && (
        <div className="cc-range">
          <span className="cc-range__lo mono">${fmt(data["52w_low"])}</span>
          <div className="cc-range__track">
            <div
              className="cc-range__fill"
              style={{
                width: `${Math.min(100, Math.max(0, (data.price - data["52w_low"]) / (data["52w_high"] - data["52w_low"]) * 100))}%`,
                background: accentColor,
              }}
            />
            <div
              className="cc-range__dot"
              style={{
                left: `${Math.min(100, Math.max(0, (data.price - data["52w_low"]) / (data["52w_high"] - data["52w_low"]) * 100))}%`,
                background: accentColor,
              }}
            />
          </div>
          <span className="cc-range__hi mono">${fmt(data["52w_high"])}</span>
          <span className="cc-range__label">52W RANGE</span>
        </div>
      )}

      {/* Position sizing */}
      <div className="cc-sizing">
        <div className="cc-sizing__title">POSITION SIZING (2% RISK RULE)</div>
        <table className="cc-sizing__table">
          <thead>
            <tr>
              <th>Portfolio</th>
              <th>Shares</th>
              <th>Notional</th>
              <th>% Alloc</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(data.position_sizes).map(([size, ps]) => (
              <tr key={size}>
                <td className="cc-sizing__port">{PORTFOLIO_LABELS[size]}</td>
                <td className="cc-sizing__shares">{ps.shares}</td>
                <td className="cc-sizing__notional">${ps.notional.toLocaleString()}</td>
                <td className="cc-sizing__pct" style={{
                  color: ps.pct > 25 ? "var(--bear)" : ps.pct > 15 ? "var(--amber)" : "var(--ink)"
                }}>{ps.pct}%</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function VixDesk({ vix }: { vix: VixData }) {
  const regimeColor = REGIME_COLOR[vix.regime] ?? "var(--amber)";
  const structureColor = vix.term_structure === "backwardation" ? "var(--bear)" :
    vix.term_structure === "contango" ? "var(--bull)" : "var(--amber)";

  return (
    <div className="cc-desk cc-desk--vix" style={{ borderTopColor: regimeColor }}>
      <div className="cc-desk__head">
        <div>
          <span className="cc-desk__sym">VIX DESK</span>
          <span className="cc-desk__name">CBOE Volatility Index</span>
        </div>
        <span className="cc-tag mono" style={{ color: regimeColor, borderColor: regimeColor }}>
          {vix.regime.toUpperCase()}
        </span>
      </div>

      {/* VIX price */}
      <div className="cc-desk__price">
        <span className="cc-desk__price-val" style={{ color: regimeColor }}>{fmt(vix.price)}</span>
        <span className="cc-desk__chg mono" style={{ color: structureColor }}>
          {vix.term_structure.toUpperCase()}
        </span>
      </div>

      {/* Term structure */}
      <div className="vix-curve">
        <div className="vix-curve__item">
          <span className="vix-curve__label">9D</span>
          <span className="vix-curve__val mono">{fmt(vix.vix_9d)}</span>
        </div>
        <div className="vix-curve__arrow">→</div>
        <div className="vix-curve__item vix-curve__item--main">
          <span className="vix-curve__label">30D (SPOT)</span>
          <span className="vix-curve__val mono" style={{ color: regimeColor, fontSize: 22 }}>
            {fmt(vix.vix_30d)}
          </span>
        </div>
        <div className="vix-curve__arrow">→</div>
        <div className="vix-curve__item">
          <span className="vix-curve__label">3M</span>
          <span className="vix-curve__val mono">{fmt(vix.vix_3m)}</span>
        </div>
      </div>

      <div className="vix-spread mono">
        Spread 3M–Spot:{" "}
        <b style={{ color: vix.spread > 0 ? "var(--bull)" : "var(--bear)" }}>
          {vix.spread > 0 ? "+" : ""}{fmt(vix.spread)} pts
        </b>
      </div>

      {/* Regime signals */}
      <div className="vix-signals">
        <div className="vix-signal">
          <span className="vix-signal__lbl">VOL SIGNAL</span>
          <span className="vix-signal__val mono" style={{
            color: vix.uvxy_signal.includes("AVOID") ? "var(--bear)" :
                   vix.uvxy_signal.includes("SHORT") ? "var(--bull)" : "var(--amber)"
          }}>{vix.uvxy_signal}</span>
        </div>
        <div className="vix-signal">
          <span className="vix-signal__lbl">TERM STRUCTURE</span>
          <span className="vix-signal__val mono" style={{ color: structureColor }}>
            {vix.term_structure.toUpperCase()}
          </span>
        </div>
      </div>

      {/* Regime advice */}
      <div className="vix-advice">
        <div className="vix-advice__title">REGIME ADVICE</div>
        <div className="vix-advice__text">{vix.regime_advice}</div>
      </div>

      {/* Regime scale */}
      <div className="vix-scale">
        {["calm", "elevated", "stress", "panic"].map((r) => (
          <div
            key={r}
            className={`vix-scale__step ${r === vix.regime ? "vix-scale__step--active" : ""}`}
            style={{ borderColor: r === vix.regime ? REGIME_COLOR[r] : undefined }}
          >
            <span style={{ color: r === vix.regime ? REGIME_COLOR[r] : undefined }}>
              {r.toUpperCase()}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

function MarketPulse({ indices, risk }: {
  indices: MarketLive["indices"];
  risk: MarketLive["risk_summary"];
}) {
  const heatColor = HEAT_COLOR[risk.market_heat] ?? "var(--amber)";
  return (
    <div className="market-pulse">
      {Object.entries(indices).map(([sym, d]) => (
        <div key={sym} className="pulse-item">
          <span className="pulse-item__sym">{sym}</span>
          <span className="pulse-item__price mono">${d.price.toLocaleString()}</span>
          <span
            className="pulse-item__chg mono"
            style={{ color: d.change_pct >= 0 ? "var(--bull)" : "var(--bear)" }}
          >
            {d.change_pct >= 0 ? "▲" : "▼"}{Math.abs(d.change_pct).toFixed(2)}%
          </span>
        </div>
      ))}
      <div className="pulse-divider" />
      <div className="pulse-item">
        <span className="pulse-item__sym">MARKET HEAT</span>
        <span className="pulse-item__price" style={{ color: heatColor }}>
          {risk.market_heat.toUpperCase()}
        </span>
      </div>
      <div className="pulse-item">
        <span className="pulse-item__sym">DEPLOY</span>
        <span className="pulse-item__price" style={{ color: heatColor }}>
          {risk.recommended_exposure_pct}%
        </span>
      </div>
    </div>
  );
}

function LoadingSkeleton() {
  return (
    <div className="cc-loading">
      <div className="cc-loading__spinner" />
      <div className="cc-loading__text mono">
        FETCHING LIVE MARKET DATA
        <span className="cc-loading__dots">...</span>
      </div>
      <div className="cc-loading__sub">
        Loading prices · VIX term structure · Computing signals · Sizing positions
      </div>
    </div>
  );
}

export function CommandCenter({ market, loading, error, onRunAnalysis }: Props) {
  if (loading && !market) return <LoadingSkeleton />;
  if (error && !market) {
    return (
      <div className="cc-error">
        <div className="cc-error__icon">⚠</div>
        <div className="cc-error__msg">Could not connect to RAG service</div>
        <div className="cc-error__detail mono">{error}</div>
        <div className="cc-error__hint">Make sure the RAG service is running on port 8001</div>
      </div>
    );
  }
  if (!market) return null;

  // V2.0 watchlist instruments (must match the rag-service market-live focus).
  const primaryTickers = ["SPCX", "NVDA", "MSFT"];
  const secondaryTickers = ["AAPL", "GOOGL", "AMZN"];

  return (
    <div className="cc-root">
      {/* Market Pulse bar */}
      <MarketPulse indices={market.indices} risk={market.risk_summary} />

      {/* Risk advice banner */}
      <div className="cc-advice-banner" style={{
        borderColor: HEAT_COLOR[market.risk_summary.market_heat]
      }}>
        <span className="cc-advice-banner__icon">⬡</span>
        <span className="cc-advice-banner__text">{market.risk_summary.hedging_advice}</span>
        <span className="cc-advice-banner__tag mono" style={{
          color: HEAT_COLOR[market.risk_summary.market_heat],
          borderColor: HEAT_COLOR[market.risk_summary.market_heat],
        }}>
          EXPOSURE: {market.risk_summary.recommended_exposure_pct}%
        </span>
      </div>

      {/* Primary row: NVDA, SPCX, VIX */}
      <div className="cc-row cc-row--primary">
        {primaryTickers.map(sym => {
          const data = market.tickers[sym];
          if (!data) return null;
          return (
            <TickerDesk
              key={sym}
              sym={sym}
              data={data}
              onAnalyze={() => onRunAnalysis(sym)}
            />
          );
        })}
        <VixDesk vix={market.vix} />
      </div>

      {/* Secondary row */}
      <div className="cc-row cc-row--secondary">
        {secondaryTickers.map(sym => {
          const data = market.tickers[sym];
          if (!data) return null;
          return (
            <TickerDesk
              key={sym}
              sym={sym}
              data={data}
              onAnalyze={() => onRunAnalysis(sym)}
            />
          );
        })}
      </div>

      {/* Timestamp */}
      <div className="cc-ts mono">
        Live data as of {new Date(market.timestamp).toLocaleString()} ·
        All signals are algorithmic, not investment advice
      </div>
    </div>
  );
}
