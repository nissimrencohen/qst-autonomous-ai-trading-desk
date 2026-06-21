/**
 * Client-side persistence so analyses + the live market snapshot survive a page
 * refresh (the backend already persists per-ticker memory to agent_memory.db;
 * this is the browser-side complement that keeps the *full* session visible).
 */
import type { ProbabilityReport, MarketLive, AlertEntry } from "./types";

const K_HISTORY = "desk01.history.v1";
const K_LAST = "desk01.lastReport.v1";
const K_MARKET = "desk01.market.v1";
const K_ALERTS = "desk01.alerts.v1";
const MAX_HISTORY = 20;
const MAX_ALERTS = 50;

function read<T>(key: string): T | null {
  try {
    const s = localStorage.getItem(key);
    return s ? (JSON.parse(s) as T) : null;
  } catch {
    return null;
  }
}

function write(key: string, val: unknown): void {
  try {
    localStorage.setItem(key, JSON.stringify(val));
  } catch {
    /* private mode / quota — non-fatal, the app still works in-memory */
  }
}

export function loadHistory(): ProbabilityReport[] {
  return read<ProbabilityReport[]>(K_HISTORY) ?? [];
}

/** Prepend a report (dedup by run_id), cap the list, persist, return the new list. */
export function appendHistory(report: ProbabilityReport): ProbabilityReport[] {
  const existing = loadHistory().filter((r) => r.run_id !== report.run_id);
  const next = [report, ...existing].slice(0, MAX_HISTORY);
  write(K_HISTORY, next);
  return next;
}

export function loadLastReport(): ProbabilityReport | null {
  return read<ProbabilityReport>(K_LAST);
}

export function saveLastReport(report: ProbabilityReport): void {
  write(K_LAST, report);
}

export function loadMarket(): MarketLive | null {
  return read<MarketLive>(K_MARKET);
}

export function saveMarket(m: MarketLive): void {
  write(K_MARKET, m);
}

export function loadAlerts(): AlertEntry[] {
  return read<AlertEntry[]>(K_ALERTS) ?? [];
}

export function appendAlert(a: AlertEntry): AlertEntry[] {
  const next = [a, ...loadAlerts()].slice(0, MAX_ALERTS);
  write(K_ALERTS, next);
  return next;
}
