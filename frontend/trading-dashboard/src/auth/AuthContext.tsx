/**
 * AuthContext — DB-backed authentication state for the trading desk.
 *
 * Handles the login request (POST /auth/token), persists the JWT + role across
 * reloads (localStorage), and exposes the current user and role helpers used to
 * gate the UI (e.g. the admin-only INGEST tab).
 */
import {
  createContext, useCallback, useContext, useEffect, useMemo, useState,
  type ReactNode,
} from "react";
import { login as apiLogin, getAuthToken, setAuthToken, type Role } from "../api";

const USER_KEY = "desk.auth.user";
const ROLE_KEY = "desk.auth.role";

export interface AuthUser {
  username: string;
  role: Role;
}

interface AuthState {
  user: AuthUser | null;
  isAuthenticated: boolean;
  isAdmin: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthState | undefined>(undefined);

function loadStoredUser(): AuthUser | null {
  try {
    const token = getAuthToken();
    const username = localStorage.getItem(USER_KEY);
    const role = localStorage.getItem(ROLE_KEY) as Role | null;
    if (token && username && (role === "admin" || role === "user")) {
      return { username, role };
    }
  } catch {
    /* storage unavailable */
  }
  return null;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(loadStoredUser);

  const login = useCallback(async (username: string, password: string) => {
    const result = await apiLogin(username, password);
    setAuthToken(result.access_token);
    try {
      localStorage.setItem(USER_KEY, result.username);
      localStorage.setItem(ROLE_KEY, result.role);
    } catch {
      /* storage unavailable — session-only */
    }
    setUser({ username: result.username, role: result.role });
  }, []);

  const logout = useCallback(() => {
    setAuthToken(null);
    try {
      localStorage.removeItem(USER_KEY);
      localStorage.removeItem(ROLE_KEY);
    } catch {
      /* noop */
    }
    setUser(null);
  }, []);

  // Cross-tab sync: log out everywhere when the token is cleared in another tab.
  useEffect(() => {
    const onStorage = (e: StorageEvent) => {
      if (e.key === "desk.auth.token" && e.newValue === null) setUser(null);
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  const value = useMemo<AuthState>(
    () => ({
      user,
      isAuthenticated: user !== null,
      isAdmin: user?.role === "admin",
      login,
      logout,
    }),
    [user, login, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within an AuthProvider");
  return ctx;
}
