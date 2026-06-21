/**
 * Login — centered terminal-aesthetic auth screen.
 *
 * Sits in front of the entire dashboard until the user authenticates against
 * the DB-backed /auth/token endpoint. Matches the deep-graphite / phosphor-amber
 * look of the desk.
 */
import { useState, type FormEvent } from "react";
import { useAuth } from "../auth/AuthContext";

export function Login() {
  const { login } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await login(username.trim(), password);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login">
      <div className="scanlines" aria-hidden="true" />
      <form className="login__card" onSubmit={onSubmit}>
        <div className="login__brand">
          QST<span className="login__brand-accent">_</span>
        </div>
        <p className="login__sub">QUANT SWARM TERMINAL — SECURE ACCESS</p>

        <label className="login__label" htmlFor="login-user">OPERATOR ID</label>
        <input
          id="login-user"
          className="login__input mono"
          type="text"
          autoComplete="username"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          autoFocus
          required
        />

        <label className="login__label" htmlFor="login-pass">PASSPHRASE</label>
        <input
          id="login-pass"
          className="login__input mono"
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
        />

        {error && <div className="login__error mono">✕ {error}</div>}

        <button className="login__submit" type="submit" disabled={busy || !username || !password}>
          {busy ? "AUTHENTICATING…" : "▶ ENTER DESK"}
        </button>

        <div className="login__hint mono">
          Credentials are verified against the desk database. Contact your
          administrator for access.
        </div>
      </form>
    </div>
  );
}
