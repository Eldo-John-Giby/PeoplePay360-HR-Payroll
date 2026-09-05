import { useState, type FormEvent } from "react";
import { Navigate, useNavigate } from "react-router-dom";

import { ApiError } from "../api/client";
import { useAuth } from "../auth";

// All seeded accounts (password for all: Password@123). Clicking a chip signs
// you straight in as that role — no need to type anything.
const DEMO_PASSWORD = "Password@123";
const DEMO_ACCOUNTS = [
  { email: "admin@oxp.com", role: "ADMIN" },
  { email: "divya.nair@oxp.com", role: "HR_MANAGER" },
  { email: "priya.singh@oxp.com", role: "HR_PAYROLL_MANAGER" },
  { email: "neha.patel@oxp.com", role: "HR_PAYROLL_USER" },
  { email: "john.dsouza@oxp.com", role: "EMPLOYEE" },
  { email: "aarav.mehta@oxp.com", role: "EMPLOYEE" },
  { email: "sara.khan@oxp.com", role: "EMPLOYEE" },
];

export function LoginPage() {
  const { user, login } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (user) return <Navigate to="/" replace />;

  async function signIn(em: string, pw: string) {
    setError(null);
    setBusy(true);
    try {
      await login(em.trim(), pw);
      navigate("/", { replace: true });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Login failed.");
    } finally {
      setBusy(false);
    }
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    void signIn(email, password);
  }

  return (
    <div className="login-wrap">
      <form className="card login-card" onSubmit={onSubmit}>
        <h1>PeoplePay360</h1>
        <p className="muted">Attendance &amp; Time Off console</p>

        <label className="field">
          <span>Email</span>
          <input
            type="email"
            required
            autoComplete="username"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="you@company.com"
          />
        </label>
        <label className="field">
          <span>Password</span>
          <input
            type="password"
            required
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="••••••••"
          />
        </label>

        {error && <div className="alert alert-error">{error}</div>}

        <button className="btn btn-primary btn-block" disabled={busy}>
          {busy ? "Signing in…" : "Sign in"}
        </button>

        <div className="demo-hints">
          <div className="muted">
            One-click demo logins (all use {DEMO_PASSWORD})
          </div>
          {DEMO_ACCOUNTS.map((a) => (
            <button
              key={a.email}
              type="button"
              className="btn btn-ghost demo-chip"
              disabled={busy}
              onClick={() => {
                setEmail(a.email);
                setPassword(DEMO_PASSWORD);
                void signIn(a.email, DEMO_PASSWORD);
              }}
            >
              {a.role} — {a.email}
            </button>
          ))}
        </div>
      </form>
    </div>
  );
}
