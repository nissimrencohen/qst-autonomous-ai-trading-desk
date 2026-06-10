import { useEffect, useState } from "react";
import { fetchTrace } from "../api";
import type { RunTrace } from "../types";

export function AgentLog(props: { runId: string | null }) {
  const [trace, setTrace] = useState<RunTrace | null>(null);

  useEffect(() => {
    setTrace(null);
    if (!props.runId) return;
    let alive = true;
    const tick = async () => {
      const t = await fetchTrace(props.runId!).catch(() => null);
      if (alive && t) setTrace(t);
      if (alive && (!t || t.finished_at === null)) setTimeout(tick, 1500);
    };
    tick();
    return () => {
      alive = false;
    };
  }, [props.runId]);

  return (
    <section className="panel panel--log" style={{ animationDelay: "0.2s" }}>
      <header className="panel__head">
        <h2>Agent Trace</h2>
        <span className={`live ${trace && !trace.finished_at ? "live--on" : ""}`}>
          <i /> {trace && !trace.finished_at ? "LIVE" : "IDLE"}
        </span>
      </header>

      {!trace ? (
        <p className="log__empty mono">awaiting run…</p>
      ) : (
        <ol className="log">
          <li className="log__line log__line--meta">
            <span className="mono">run {trace.run_id}</span> · {trace.ticker} ·{" "}
            {trace.started_at}
          </li>
          {trace.steps.map((s, i) => {
            const { at, step, ...rest } = s;
            return (
              <li className="log__line" key={i} style={{ animationDelay: `${i * 0.08}s` }}>
                <span className="log__time mono">{String(at).slice(11, 19)}</span>
                <span className="log__step">{String(step)}</span>
                <span className="log__detail mono">{JSON.stringify(rest)}</span>
              </li>
            );
          })}
          {trace.finished_at && (
            <li className="log__line log__line--meta">finished {trace.finished_at}</li>
          )}
        </ol>
      )}
    </section>
  );
}
