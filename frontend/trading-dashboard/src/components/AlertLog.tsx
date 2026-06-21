import type { AlertEntry } from "../types";

const KIND_LABEL: Record<AlertEntry["kind"], string> = {
  regime: "REGIME",
  heat:   "HEAT",
};

export function AlertLog(props: { alerts: AlertEntry[] }) {
  if (props.alerts.length === 0) return null;

  return (
    <section className="panel panel--alerts">
      <header className="panel__head">
        <h2>Alert Log</h2>
        <span className="panel__tag panel__tag--alert">
          {props.alerts.length} EVENT{props.alerts.length !== 1 ? "S" : ""}
        </span>
      </header>
      <ul className="alert-log">
        {props.alerts.map((a) => (
          <li key={a.id} className="alert-log__item mono">
            <span className="alert-log__time">{new Date(a.at).toLocaleTimeString()}</span>
            <span className={`alert-log__kind alert-log__kind--${a.kind}`}>{KIND_LABEL[a.kind]}</span>
            <span className="alert-log__delta">
              {a.prev.toUpperCase()} → <b>{a.curr.toUpperCase()}</b>
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}
