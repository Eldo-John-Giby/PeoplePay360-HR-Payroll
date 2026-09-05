import { Link, Navigate, useNavigate } from "react-router-dom";
import { useState, type FormEvent } from "react";

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

function BrandMark() {
  return (
    <span className="login-mark" aria-hidden="true">
      P
    </span>
  );
}

export function LoginPage() {
  const { user, login } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (user) return <Navigate to="/attendance" replace />;

  async function signIn(em: string, pw: string) {
    setError(null);
    setBusy(true);
    try {
      await login(em.trim(), pw);
      navigate("/attendance", { replace: true });
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
    <div className="login-page">
      {/* Left sidebar */}
      <aside className="login-side">
        <Link to="/" className="login-brand">
          <BrandMark />
          <span>PeoplePay360</span>
        </Link>

        <div className="login-side-body">
          <h2>Attendance &amp; time off, all in one place.</h2>
          <p>
            Check in, ask for leave, approve requests and watch live balances —
            without a single spreadsheet.
          </p>
          <ul className="login-features">
            <li>Smart check-in / check-out with late &amp; overtime flags</li>
            <li>Live leave balances — never pre-deducted, never stale</li>
            <li>Role-based access for employees, HR and payroll</li>
          </ul>
        </div>

        <div className="login-side-foot">
          <Link className="login-home" to="/">
            ← Back to home
          </Link>
          <span className="login-side-note">Free demo · seeded data</span>
        </div>
      </aside>

      {/* Right panel with the login card */}
      <div className="login-wrap">
        <form className="login-card" onSubmit={onSubmit}>
          <div className="login-card-head">
            <BrandMark />
            <h1>Welcome back</h1>
            <p className="muted">Sign in to your attendance &amp; time off console</p>
          </div>

          <label className="field">
            <span>Email</span>
            <input
              type="email"
              required
              autoComplete="username"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@oxp.com"
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

          <div className="login-divider">
            <span>or use a demo account</span>
          </div>

          <div className="demo-hints">
            {DEMO_ACCOUNTS.map((a) => (
              <button
                key={a.email}
                type="button"
                className="btn btn-ghost demo-chip"
                disabled={busy}
                onClick={() => void signIn(a.email, DEMO_PASSWORD)}
              >
                <span className="chip-role">{a.role}</span>
                <span className="chip-email">{a.email}</span>
              </button>
            ))}
          </div>
        </form>
      </div>
    </div>
  );
}