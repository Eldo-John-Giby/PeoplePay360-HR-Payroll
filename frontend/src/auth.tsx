// Auth context: keeps the logged-in user (with roles + linked employee) in
// React state after login and across refreshes, and exposes role helpers for
// the role-based nav.

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";

import { fetchMe, login as apiLogin, logout as apiLogout } from "./api/client";
import type { Me } from "./api/types";

interface AuthContextValue {
  user: Me | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
  hasRole: (...roles: string[]) => boolean;
  isHr: boolean;
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

const HR_ROLES = [
  "HR_MANAGER",
  "HR_PAYROLL_USER",
  "HR_PAYROLL_MANAGER",
  "ADMIN",
];

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const token = localStorage.getItem("pp360.access_token");
    if (!token) {
      setLoading(false);
      return;
    }
    fetchMe()
      .then(setUser)
      .catch(() => {
        // Bad/expired token — drop it and send the user to the login screen.
        apiLogout();
      })
      .finally(() => setLoading(false));
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    const tokens = await apiLogin(email, password);
    localStorage.setItem("pp360.access_token", tokens.access_token);
    setUser(await fetchMe());
  }, []);

  const logout = useCallback(() => {
    setUser(null);
    apiLogout();
  }, []);

  const hasRole = useCallback(
    (...roles: string[]) =>
      Boolean(user && user.roles.some((r) => roles.includes(r.name))),
    [user],
  );

  const value: AuthContextValue = {
    user,
    loading,
    login,
    logout,
    hasRole,
    isHr: Boolean(user && user.roles.some((r) => HR_ROLES.includes(r.name))),
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}

// ---------------------------------------------------------------------------
// Small formatting helpers used across pages
// ---------------------------------------------------------------------------

export function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString();
}

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(`${iso}T00:00:00`);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString();
}

export function fmtHours(value: string | null | undefined): string {
  if (value === null || value === undefined) return "—";
  const n = Number(value);
  return Number.isNaN(n) ? value : `${n.toFixed(2)} h`;
}

/** Convert a datetime-local input value into a naive ISO timestamp the API
 * interprets in its UTC frame (see backend service docstring). */
export function naiveIso(datetimeLocal: string): string {
  return datetimeLocal ? `${datetimeLocal}:00` : "";
}

/** Format an API datetime for a datetime-local input. */
export function toLocalInput(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function todayIso(): string {
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

export function addDaysIso(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() + days);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}
