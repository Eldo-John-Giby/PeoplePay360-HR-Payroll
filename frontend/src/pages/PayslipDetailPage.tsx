// Payslip detail — full breakdown (lines + warnings) for payroll roles.
// EMPLOYEE self-service cannot read the detail endpoint (backend RBAC), so
// the page offers their PDF directly when they own the payslip.

import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import "../styles.css"
import {
  ApiError,
  downloadPayslipPdf,
  getPayslip,
} from "../api/client";
// PayslipDetail is the committed API name for the full payslip breakdown.
import type { PayslipDetail as Payslip } from "../api/types";
import { fmtDate, fmtMoney, useAuth } from "../auth";
import { PayrunStatusBadge } from "./PayrunsPage";

const CATEGORY_LABEL: Record<string, string> = {
  basic: "Basic Salary",
  allowance: "Allowance",
  deduction: "Deduction",
  gross: "Gross Pay",
  contribution: "Contribution",
  net: "Net Pay",
};

export function PayslipDetailPage() {
  const { id: paramId, payslipId } = useParams();
  const id = Number(paramId ?? payslipId);
  const { hasRole } = useAuth();
  const isPayroll = hasRole("HR_PAYROLL_USER", "HR_PAYROLL_MANAGER", "ADMIN");

  const [slip, setSlip] = useState<Payslip | null>(null);
  const [error, setError] = useState("");
  const [downloading, setDownloading] = useState(false);

  const load = useCallback(async () => {
    if (!Number.isFinite(id) || !isPayroll) return;
    setError("");
    try {
      setSlip(await getPayslip(id));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }, [id, isPayroll]);

  useEffect(() => {
    void load();
  }, [load]);

  async function onPdf() {
    setDownloading(true);
    setError("");
    try {
      await downloadPayslipPdf(id);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setDownloading(false);
    }
  }

  if (!isPayroll) {
    return (
      <div style={{ maxWidth: 560, margin: "0 auto", paddingTop: 24 }}>
        <div className="card" style={{ padding: 24 }}>
          <div style={{ textAlign: "center", marginBottom: 20 }}>
            <div className="user-avatar" style={{ width: 56, height: 56, fontSize: 24, margin: "0 auto 12px", display: "block" }}>
              📄
            </div>
            <h2 style={{ margin: 0, fontSize: 20 }}>Payslip #{id}</h2>
            <p className="muted" style={{ marginTop: 8, fontSize: 14 }}>
              Employee self-service view
            </p>
          </div>
          <p style={{ textAlign: "center", color: "var(--muted)", marginBottom: 20 }}>
            Payslip details are available to payroll staff. As an employee you
            can download your own payslip PDF — the backend checks ownership.
          </p>
          <div className="stack" style={{ gap: 12, maxWidth: 320, margin: "0 auto" }}>
            <button className="btn btn-primary btn-block" disabled={downloading} onClick={() => void onPdf()}>
              {downloading ? "Downloading…" : "📥 Download PDF"}
            </button>
            {error ? <div className="alert alert-error">{error}</div> : null}
          </div>
          <p className="small muted" style={{ textAlign: "center", marginTop: 16 }}>
            <Link to="/payroll/payslips">← Back to my payslips</Link>
          </p>
        </div>
      </div>
    );
  }

  const earnings = slip.lines.filter((l) => l.category === "basic" || l.category === "allowance");
  const deductions = slip.lines.filter((l) => l.category === "deduction" || l.category === "contribution");

  return (
    <div style={{ animation: "fadeIn 0.3s ease" }}>
      {/* Header */}
      <div className="row spread" style={{ marginBottom: 20 }}>
        <h2>
          <Link to="/payroll/payslips" className="muted" style={{ fontSize: 14, display: "flex", alignItems: "center", gap: 4 }}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="m15 18-6-6 6-6" />
            </svg>
            Payslips
          </Link>
          <span style={{ marginLeft: 8, color: "var(--muted-faint)" }}>/</span>
          <span style={{ color: "var(--ink)" }}>Payslip #{id}</span>
        </h2>
        <div style={{ display: "flex", gap: 10 }}>
          {slip && <PayrunStatusBadge status={slip.status} />}
          <button className="btn btn-primary" disabled={downloading} onClick={() => void onPdf()} style={{ minWidth: 140 }}>
            {downloading ? "Downloading…" : "📥 Download PDF"}
          </button>
        </div>
      </div>

      {error ? <div className="alert alert-error" style={{ marginBottom: 16 }}>{error}</div> : null}

      {!slip ? (
        <div className="center" style={{ flexDirection: "column", gap: 12 }}>
          <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="var(--muted)" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M12 2v20M2 12h20" />
          </svg>
          <p className="muted">Loading payslip…</p>
        </div>
      ) : (
        <div className="stack" style={{ gap: 20 }}>
          {/* Employee Info Card */}
          <div className="card" style={{ background: "linear-gradient(135deg, #f8fafc 0%, #f1f5f9 100%)" }}>
            <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", flexWrap: "wrap", gap: 16 }}>
              <div style={{ flex: 1, minWidth: 200 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 16 }}>
                  <div className="user-avatar" style={{ width: 48, height: 48, fontSize: 20 }}>
                    {slip.employee_name.charAt(0).toUpperCase()}
                  </div>
                  <div>
                    <h3 style={{ margin: 0, fontSize: 16, fontWeight: 600 }}>{slip.employee_name}</h3>
                    <p className="muted small" style={{ margin: 0 }}>Employee ID: #{slip.employee_id}</p>
                  </div>
                </div>
                <div className="kv" style={{ margin: 0, gridTemplateColumns: "140px 1fr", gap: "6px 16px" }}>
                  <dt style={{ color: "var(--muted)", fontSize: 12, textTransform: "uppercase", letterSpacing: "0.5px" }}>Period</dt>
                  <dd style={{ fontSize: 14 }}>
                    <span style={{ fontWeight: 500 }}>{fmtDate(slip.period_start)}</span>
                    <span style={{ color: "var(--muted-faint)", margin: "0 4px" }}>→</span>
                    <span style={{ fontWeight: 500 }}>{fmtDate(slip.period_end)}</span>
                  </dd>

                  <dt style={{ color: "var(--muted)", fontSize: 12, textTransform: "uppercase", letterSpacing: "0.5px" }}>Worked Days</dt>
                  <dd style={{ fontSize: 14 }}>{slip.worked_days} days</dd>

                  <dt style={{ color: "var(--muted)", fontSize: 12, textTransform: "uppercase", letterSpacing: "0.5px" }}>Payrun</dt>
                  <dd style={{ fontSize: 14 }}>
                    <Link to={`/payroll/payruns/${slip.payrun_id}`} style={{ color: "var(--brand)", textDecoration: "none", fontWeight: 500 }}>
                      Payrun #{slip.payrun_id}
                    </Link>
                  </dd>
                </div>
              </div>

              {/* Net Pay Highlight */}
              <div style={{ textAlign: "right", padding: "8px 0" }}>
                <p className="muted small" style={{ textTransform: "uppercase", letterSpacing: "0.5px", marginBottom: 4 }}>Net Salary</p>
                <p style={{ margin: 0, fontSize: 32, fontWeight: 700, color: "var(--brand)", letterSpacing: "-0.5px" }}>
                  {fmtMoney(slip.net_salary)}
                </p>
              </div>
            </div>
          </div>

          {/* Salary Breakdown */}
          <div>
            <h3 style={{ marginBottom: 12, fontSize: 15, fontWeight: 600, color: "var(--ink)" }}>
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ marginRight: 8, verticalAlign: "middle", color: "var(--brand)" }}>
                <path d="M12 1v22M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6" />
              </svg>
              Salary Breakdown
              <span className="muted small" style={{ marginLeft: 8, fontSize: 13 }}>({slip.lines.length} items)</span>
            </h3>

            {/* Earnings Section */}
            <div className="card" style={{ marginBottom: 12 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8, padding: "0 4px" }}>
                <span style={{ width: 4, height: 16, borderRadius: 2, background: "var(--ok)", display: "inline-block" }} />
                <span style={{ fontSize: 12, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.5px", color: "var(--ok)" }}>Earnings</span>
              </div>
              <table className="table" style={{ margin: 0, fontSize: 14 }}>
                <thead>
                  <tr>
                    <th style={{ width: 40, fontSize: 11 }}>#</th>
                    <th>Component</th>
                    <th style={{ width: 100, textAlign: "right" }}>Amount</th>
                  </tr>
                </thead>
                <tbody>
                  {earnings.map((l) => (
                    <tr key={l.id}>
                      <td className="muted" style={{ fontFeatures: "tnum" }}>{l.sequence}</td>
                      <td>
                        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                          <span className={`badge badge-${l.category}`} style={{ fontSize: 11, padding: "2px 8px" }}>
                            {l.code}
                          </span>
                          <span style={{ color: "var(--ink)" }}>{l.name}</span>
                        </div>
                      </td>
                      <td style={{ textAlign: "right", fontWeight: 600, fontFeatures: "tnum" }}>
                        <span style={{ color: l.amount.startsWith("-") ? "var(--danger)" : "var(--ink)" }}>
                          {fmtMoney(l.amount)}
                        </span>
                      </td>
                    </tr>
                  ))}
                  {earnings.length === 0 && (
                    <tr>
                      <td colSpan={3} className="muted" style={{ textAlign: "center", padding: "20px 0" }}>
                        No earnings items
                      </td>
                    </tr>
                  )}
                </tbody>
                <tfoot>
                  {slip.gross_salary !== "0" && (
                    <tr>
                      <td colSpan={2} style={{ padding: "10px 12px", background: "#f8fafc", fontWeight: 600 }}>
                        Gross Pay
                      </td>
                      <td style={{ textAlign: "right", padding: "10px 12px", background: "#f8fafc", fontWeight: 700, fontSize: 15, color: "var(--ink)", fontFeatures: "tnum" }}>
                        {fmtMoney(slip.gross_salary)}
                      </td>
                    </tr>
                  )}
                </tfoot>
              </table>
            </div>

            {/* Deductions Section */}
            {deductions.length > 0 && (
              <div className="card">
                <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8, padding: "0 4px" }}>
                  <span style={{ width: 4, height: 16, borderRadius: 2, background: "var(--danger)", display: "inline-block" }} />
                  <span style={{ fontSize: 12, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.5px", color: "var(--danger)" }}>Deductions</span>
                </div>
                <table className="table" style={{ margin: 0, fontLegend: 14 }}>
                  <thead>
                    <tr>
                      <th style={{ width: 40, fontSize: 11 }}>#</th>
                      <th>Component</th>
                      <th style={{ width: 100, textAlign: "right" }}>Amount</th>
                    </tr>
                  </thead>
                  <tbody>
                    {deductions.map((l) => (
                      <tr key={l.id}>
                        <td className="muted" style={{ fontFeatures: "tnum" }}>{l.sequence}</td>
                        <td>
                          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                            <span className={`badge badge-${l.category}`} style={{ fontSize: 11, padding: "2px 8px" }}>
                              {l.code}
                            </span>
                            <span style={{ color: "var(--ink)" }}>{l.name}</span>
                          </div>
                        </td>
                        <td style={{ textAlign: "right", fontWeight: 600, fontFeatures: "tnum", color: "var(--danger)" }}>
                          -{fmtMoney(l.amount).replace(/^-/, "")}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          {/* Final Summary Bar */}
          <div className="card" style={{ background: "linear-gradient(135deg, var(--brand) 0%, var(--brand-dark) 100%)", color: "#fff" }}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: 16 }}>
              <div>
                <p style={{ margin: 0, fontSize: 12, opacity: 0.85, textTransform: "uppercase", letterSpacing: "0.5px" }}>Final Pay</p>
                <p style={{ margin: "4px 0 0", fontSize: 24, fontWeight: 700, letterSpacing: "-0.5px" }}>
                  {fmtMoney(slip.net_salary)}
                </p>
              </div>
              <div style={{ textAlign: "right" }}>
                <div style={{ display: "flex", justifyContent: "flex-end", gap: 24, fontSize: 13 }}>
                  <div>
                    <p style={{ margin: 0, fontSize: 11, opacity: 0.7 }}>Gross</p>
                    <p style={{ margin: 0, fontWeight: 600 }}>{fmtMoney(slip.gross_salary)}</p>
                  </div>
                  <div style={{ width: 1, height: 30, background: "rgba(255,255,255,0.2)" }} />
                  <div>
                    <p style={{ margin: 0, fontSize: 11, opacity: 0.7 }}>Total Deducted</p>
                    <p style={{ margin: 0, fontWeight: 600 }}>
                      -{fmtMoney((Number(slip.gross_salary) - Number(slip.net_salary)).toString())}
                    </p>
                  </div>
                </div>
              </div>
            </div>
          </div>

          {/* Warnings Section */}
          {slip.warnings.length > 0 && (
            <div>
              <h3 style={{ marginBottom: 12, fontSize: 15, fontWeight: 600, color: "var(--ink)" }}>
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ marginRight: 8, verticalAlign: "middle", color: "var(--warn)" }}>
                  <path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z" />
                  <path d="M12 9v4" />
                  <path d="M12 17h.01" />
                </svg>
                Warnings ({slip.warnings.length})
              </h3>
              <div className="cards" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))", gap: 12 }}>
                {slip.warnings.map((w) => (
                  <div key={w.id} className="card" style={{ padding: 14, borderLeft: "4px solid var(--warn)", background: "var(--warn-bg)" }}>
                    <div style={{ display: "flex", alignItems: "flex-start", gap: 10 }}>
                      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="var(--warn)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0, marginTop: 1 }}>
                        <path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z" />
                        <path d="M12 9v4" />
                        <path d="M12 17h.01" />
                      </svg>
                      <div>
                        <span style={{ fontSize: 11, fontWeight: 600, color: "var(--warn)", textTransform: "uppercase", letterSpacing: "0.5px" }}>
                          {w.warning_type.replaceAll("_", " ")}
                        </span>
                        <p style={{ margin: "4px 0 0", fontSize: 13, color: "var(--ink)" }}>{w.message}</p>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
