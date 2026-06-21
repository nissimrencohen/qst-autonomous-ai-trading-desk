import { Component, type ReactNode } from "react";

interface Props { name: string; children: ReactNode; }
interface State { error: Error | null; }

/**
 * Isolates a view so a runtime error renders an inline message instead of
 * blanking the whole dashboard (there was no boundary before — a single crash
 * white-screened every view).
 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: unknown) {
    // Surfaced in the browser console for debugging.
    console.error(`[ErrorBoundary:${this.props.name}]`, error, info);
  }

  render() {
    if (this.state.error) {
      return (
        <div style={{
          fontFamily: "'Consolas','Monaco',monospace",
          border: "1px solid var(--bear,#ef4444)", borderRadius: 4,
          padding: 16, margin: "12px 0", color: "var(--text-secondary,#94a3b8)",
        }}>
          <div style={{ color: "var(--bear,#ef4444)", fontWeight: "bold", letterSpacing: 1, marginBottom: 6 }}>
            ⚠ {this.props.name} failed to render
          </div>
          <div style={{ fontSize: 11, marginBottom: 10 }}>
            {String(this.state.error?.message || this.state.error)}
          </div>
          <button
            onClick={() => this.setState({ error: null })}
            style={{
              padding: "4px 10px", fontSize: 10, cursor: "pointer", background: "transparent",
              border: "1px solid var(--border-default,#334155)", borderRadius: 3,
              color: "var(--text-tertiary,#64748b)", letterSpacing: 1,
            }}
          >↻ RETRY</button>
        </div>
      );
    }
    return this.props.children;
  }
}
