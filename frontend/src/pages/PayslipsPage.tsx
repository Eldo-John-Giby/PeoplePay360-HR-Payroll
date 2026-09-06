// Payslips list. Payroll roles see the full register with filters;
// EMPLOYEE self-service sees only their own payslips (/payslips/me) with
// PDF download — mirroring the backend's RBAC split.

import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import "../styles.css"
import {
  ApiError,
  downloadPayslipPdf,
  listMyPayslips,
  listPayslips,
} from "../api/client";
import type { PayslipSummaryItem, PayrunStatus } from "../api/types";
import { fmtDate, fmtMoney, useAuth } from "../auth";
import { PayrunStatusBadge } from "./PayrunsPage";

const VIEW_TABLE = "table";
const VIEW_CARD = "card";

export function PayslipsPage() {
  const { hasRole } = useAuth();
  const isPayroll = hasRole("HR_PAYROLL_USER", "HR_PAYROLL_MANAGER", "ADMIN");

  const [slips, setSlips] = useState<PayslipSummaryItem[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [statusFilter, setStatusFilter] = useState("");
  const [payrunFilter, setPayrunFilter] = useState("");
  const [employeeFilter, setEmployeeFilter] = useState("");
  const [downloading, setDownloading] = useState<number | null>(null);
  const [viewMode, setViewMode] = useState<"table" | "card">(VIEW_TABLE);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const page = isPayroll
        ? await listPayslips({
            status: (statusFilter as PayrunStatus) || undefined,
            payrun_id: payrunFilter ? Number(payrunFilter) : undefined,
            employee_id: employeeFilter ? Number(employeeFilter) : undefined,
          })
        : await listMyPayslips();
      setSlips(page.items);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [isPayroll, statusFilter, payrunFilter, employeeFilter]);

  useEffect(() => {
    void load();
  }, [load]);

  async function onPdf(id: number) {
    setDownloading(id);
    setError("");
    try {
      await downloadPayslipPdf(id);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setDownloading(null);
    }
  }

  return (
    <div>
      <div className="row spread" style={{ marginBottom: 16 }}>
        <h2>{isPayroll ? "Payslips" : "My payslips"}</h2>
        {isPayroll && (
          <div className="seg" style={{ marginLeft: "auto" }}>
            <button
              type="button"
              className={viewMode === VIEW_TABLE ? "on" : ""}
              onClick={() => setViewMode(VIEW_TABLE)}
            >
              Table
            </button>
            <button
              type="button"
              className={viewMode === VIEW_CARD ? "on" : ""}
              onClick={() => setViewMode(VIEW_CARD)}
            >
              Cards
            </button>
          </div>
        )}
      </div>

      {error ? <div className="alert alert-error">{error}</div> : null}

      {isPayroll && (
        <div className="card" style={{ padding: 12, marginBottom: 16 }}>
          <div className="form-grid" style={{ marginBottom: 0 }}>
            <label className="field">
              <span>Status</span>
              <select
                value={statusFilter}
                onChange={(e) => setStatusFilter(e.target.value)}
              >
                <option value="">All</option>
                {(() =>
                  (["draft", "computed", "validated", "paid", "cancelled"] as PayrunStatus[]).map((s) => (
                    <option key={s} value={s}>
                      {s}
                    </option>
                  )))()}
              </select>
            </label>
            <label className="field">
              <span>Payrun id</span>
              <input
                type="number"
                placeholder="e.g. 2"
                value={payrunFilter}
                onChange={(e) => setPayrunFilter(e.target.value)}
              />
            </label>
            <label className="field">
              <span>Employee id</span>
              <input
                type="number"
                value={employeeFilter}
                onChange={(e) => setEmployeeFilter(e.target.value)}
              />
            </label>
          </div>
        </div>
      )}

      {loading ? (
        <div className="center" style={{ flexDirection: "column", gap: 12 }}>
          <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="var(--muted)" strokeWidth="1.5">
            <path d="M12 2v20M2 12h20" />
          </svg>
          <p className="muted">Loading payslips…</p>
        </div>
      ) : slips.length === 0 ? (
        <div className="card" style={{ padding: 40, textAlign: "center" }}>
          <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="var(--muted-faint)" strokeWidth="1.5" style={{ margin: "0 auto 12px" }}>
            <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
            <polyline points="14 2 14 8 20 8" />
          </svg>
          <p className="muted">
            {isPayroll
              ? "No payslips match the filters."
              : "No payslips yet — they appear here once HR computes a payrun that includes you."}
          </p>
        </div>
      ) : viewMode === VIEW_TABLE ? (
        <table className="table">
          <thead>
            <tr>
              <th>Employee</th>
              <th>Period</th>
              <th>Gross</th>
              <th>Net</th>
              <th>Status</th>
              {isPayroll && <th>Warnings</th>}
              <th style={{ width: 140 }} />
            </tr>
          </thead>
          <tbody>
            {slips.map((p) => (
              <tr key={p.id}>
                <td>
                  <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                    <div className="user-avatar" style={{ width: 32, height: 32, fontSize: 13 }}>
                      {p.employee_name.charAt(0).toUpperCase()}
                    </div>
                    <div>
                      <b style={{ color: "var(--ink)" }}>{p.employee_name}</b>
                      <div className="small muted">ID #{p.employee_id}</div>
                    </div>
                  </div>
                </td>
                <td>
                  <span className="small">{fmtDate(p.period_start)}</span>
                  <span className="muted-faint" style={{ margin: "0 4px" }}>→</span>
                  <span className="small">{fmtDate(p.period_end)}</span>
                </td>
                <td>{fmtMoney(p.gross_salary)}</td>
                <td>
                  <b style={{ color: "var(--brand)", fontSize: 15 }}>{fmtMoney(p.net_salary)}</b>
                </td>
                <td>
                  <PayrunStatusBadge status={p.status} />
                </td>
                {isPayroll && (
                  <td>
                    {p.warning_count > 0 ? (
                      <span className="badge badge-req-to_approve">
                        {p.warning_count} warning{p.warning_count > 1 ? "s" : ""}
                      </span>
                    ) : (
                      <span className="muted">✓ Clear</span>
                    )}
                  </td>
                )}
                <td>
                  <div className="row-actions">
                    <button
                      className="btn btn-ghost btn-sm"
                      disabled={downloading === p.id}
                      onClick={() => void onPdf(p.id)}
                    >
                      {downloading === p.id ? "…" : "📥 PDF"}
                    </button>
                    {isPayroll && (
                      <Link className="btn btn-ghost btn-sm" to={`/payroll/payslips/${p.id}`}>
                        Details
                      </Link>
                    )}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <div className="cards" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(320px, 1fr))", gap: 16 }}>
          {slips.map((p) => (
            <div key={p.id} className="card" style={{ padding: 18 }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 12 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                  <div className="user-avatar" style={{ width: 40, height: 40, fontSize: 16 }}>
                    {p.employee_name.charAt(0).toUpperCase()}
                  </div>
                  <div>
                    <h4 style={{ margin: 0, fontSize: 15, fontWeight: 600 }}>{p.employee_name}</h4>
                    <p className="small muted" style={{ margin: 0 }}>ID #{p.employee_id}</p>
                  </div>
                </div>
                <PayrunStatusBadge status={p.status} />
              </div>

              <div className="kv" style={{ margin: 0, gridTemplateColumns: "100px 1fr", gap: "4px 12px", fontSize: 13 }}>
                <dt className="muted" style={{ textTransform: "uppercase", fontSize: 11, letterSpacing: "0.5px" }}>Period</dt>
                <dd style={{ fontSize: 13 }}>
                  {fmtDate(p.period_start)} → {fmtDate(p.period_end)}
                </dd>

                <dt className="muted" style={{ textTransform: "uppercase", fontSize: 11, letterSpacing: "0.5px" }}>Gross</dt>
                <dd>{fmtMoney(p.gross_salary)}</dd>

                <dt className="muted" style={{ textTransform: "uppercase", fontSize: 11, letterSpacing: "0.5px" }}>Net</dt>
                <dd>
                  <b style={{ color: "var(--brand)", fontSize: 16, fontWeight: 700 }}>{fmtMoney(p.net_salary)}</b>
                </dd>
              </div>

              {isPayroll && p.warning_count > 0 && (
                <div style={{ marginTop: 10, padding: "6px 10px", background: "var(--warn-bg)", borderRadius: 6, fontSize: 12, color: "var(--warn)", display: "flex", alignItems: "center", gap: 6 }}>
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z" />
                    <path d="M12 9v4" />
                    <path d="M12 17h.01" />
                  </svg>
                  {p.warning_count} warning{p.warning_count > 1 ? "s" : ""}
                </div>
              )}

              <div style={{ display: "flex", gap: 8, marginTop: 14, paddingTop: 12, borderTop: "1px solid var(--line)" }}>
                <button
                  className="btn btn-ghost btn-sm"
                  disabled={downloading === p.id}
                  onClick={() => void onPdf(p.id)}
                  style={{ flex: 1 }}
                >
                  {downloading === p.id ? "…" : "📥 PDF"}
                </button>
                {isPayroll && (
                  <Link className="btn btn-primary btn-sm" to={`/payroll/payslips/${p.id}`} style={{ flex: 1 }}>
                    View Details
                  </Link>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
