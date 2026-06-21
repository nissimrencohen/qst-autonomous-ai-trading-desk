import { useEffect } from "react";
import type { AlertEntry } from "../types";

const KIND_LABEL: Record<AlertEntry["kind"], string> = {
  regime: "VIX REGIME SHIFT",
  heat:   "MARKET HEAT CHANGE",
};

export function AlertToast(props: { alerts: AlertEntry[]; onDismiss: (id: string) => void }) {
  const latest = props.alerts[0];
  useEffect(() => {
    if (!latest) return;
    const t = setTimeout(() => props.onDismiss(latest.id), 8000);
    return () => clearTimeout(t);
  }, [latest?.id]);

  if (!latest) return null;

  return (
    <div className="alert-toast" role="alert">
      <div className="alert-toast__head">
        <span className="alert-toast__kind mono">{KIND_LABEL[latest.kind]}</span>
        <button className="alert-toast__close" onClick={() => props.onDismiss(latest.id)}>✕</button>
      </div>
      <p className="alert-toast__body mono">
        {latest.prev.toUpperCase()} → <b>{latest.curr.toUpperCase()}</b>
      </p>
      <span className="alert-toast__time mono">{new Date(latest.at).toLocaleTimeString()}</span>
    </div>
  );
}
