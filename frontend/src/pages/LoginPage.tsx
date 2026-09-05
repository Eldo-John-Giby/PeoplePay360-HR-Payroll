import { Navigate, useNavigate } from "react-router-dom";
import { useState, type FormEvent } from "react";

import { ApiError } from "../api/client";
import { useAuth } from "../auth";

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
      {/* Login card only */}
      <div className="login-wrap">
        <form className="login-card" onSubmit={onSubmit}>
          <div className="login-card-head">
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
        </form>
      </div>
    </div>
  );
}