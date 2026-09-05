// Payslips list. Payroll roles see the full register with filters;
// EMPLOYEE self-service sees only their own payslips (/payslips/me) with
// PDF download — mirroring the backend's RBAC split.

import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";

import {
  ApiError,
  downloadPayslipPdf,
  listMyPayslips,
  listPayslips,
} from "../api/client";
import type { PayslipSummaryItem, PayrunStatus } from "../api/types";
import { fmtDate, fmtMoney, useAuth } from "../auth";
import { PayrunStatusBadge } from "./PayrunsPage";

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
      <div className="row spread">
        <h2>{isPayroll ? "Payslips" : "My payslips"}</h2>
      </div>

      {error ? <div className="alert alert-error">{error}</div> : null}

      {isPayroll && (
        <div className="card" style={{ padding: 12, marginTop: 8 }}>
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
        <p className="muted">Loading…</p>
      ) : (
        <table className="table" style={{ marginTop: 12 }}>
          <thead>
            <tr>
              <th>Employee</th>
              <th>Period</th>
              <th>Gross</th>
              <th>Net</th>
              <th>Status</th>
              {isPayroll && <th>Warnings</th>}
              <th />
            </tr>
          </thead>
          <tbody>
            {slips.length === 0 ? (
              <tr>
                <td colSpan={7} className="muted">
                  {isPayroll
                    ? "No payslips match the filters."
                    : "No payslips yet — they appear here once HR computes a payrun that includes you."}
                </td>
              </tr>
            ) : (
              slips.map((p) => (
                <tr key={p.id}>
                  <td>{p.employee_name}</td>
                  <td>
                    {fmtDate(p.period_start)} → {fmtDate(p.period_end)}
                  </td>
                  <td>{fmtMoney(p.gross_salary)}</td>
                  <td>
                    <b>{fmtMoney(p.net_salary)}</b>
                  </td>
                  <td>
                    <PayrunStatusBadge status={p.status} />
                  </td>
                  {isPayroll && (
                    <td>
                      {p.warning_count > 0 ? (
                        <span className="badge badge-req-to_approve">
                          {p.warning_count}
                        </span>
                      ) : (
                        <span className="muted">—</span>
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
                        {downloading === p.id ? "…" : "PDF"}
                      </button>
                      {isPayroll && (
                        <Link className="btn btn-ghost btn-sm" to={`/payroll/payslips/${p.id}`}>
                          Details
                        </Link>
                      )}
                    </div>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      )}
    </div>
  );
}
