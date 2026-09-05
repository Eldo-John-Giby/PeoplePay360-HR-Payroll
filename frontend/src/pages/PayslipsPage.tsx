import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";

import {
  ApiError,
  listMyPayslips,
  listPayslips,
} from "../api/client";
import type { PayrunStatus, PayslipSummaryItem } from "../api/types";
import { fmtDate, useAuth } from "../auth";
import { fmtMoney } from "./ContractsPage";

const STATUS_LABEL: Record<PayrunStatus, string> = {
  draft: "Draft",
  computed: "Computed",
  validated: "Validated",
  paid: "Paid",
  cancelled: "Cancelled",
};

const STATUS_CLASS: Record<PayrunStatus, string> = {
  draft: "badge-muted",
  computed: "badge-overtime",
  validated: "badge-warn",
  paid: "badge-ok",
  cancelled: "badge-req-cancelled",
};

export function PayslipsPage() {
  const { hasRole, user } = useAuth();
  const isMine = !hasRole("HR_PAYROLL_USER", "HR_PAYROLL_MANAGER", "ADMIN");
  const [rows, setRows] = useState<PayslipSummaryItem[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const page = isMine ? await listMyPayslips() : await listPayslips();
      setRows(page.items);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load payslips.");
    }
  }, [isMine]);

  useEffect(() => {
    void load();
  }, [load]);

  const periodLabel = (p: PayslipSummaryItem) =>
    `${fmtDate(p.period_start)} → ${fmtDate(p.period_end)}`;

  return (
    <div className="stack">
      <div className="row spread">
        <h2>{isMine ? "My payslips" : "Payslips"}</h2>
        {isMine && user?.employee && (
          <span className="muted small">{user.employee.full_name}</span>
        )}
      </div>
      {error && <div className="alert alert-error">{error}</div>}

      <table className="table">
        <thead>
          <tr>
            <th>Employee</th>
            <th>Period</th>
            <th>Gross</th>
            <th>Net</th>
            <th>Status</th>
            <th>Warnings</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((p) => (
            <tr key={p.id}>
              <td><b>{p.employee_name}</b></td>
              <td>{periodLabel(p)}</td>
              <td>{fmtMoney(p.gross_salary)}</td>
              <td><b>{fmtMoney(p.net_salary)}</b></td>
              <td>
                <span className={`badge ${STATUS_CLASS[p.status]}`}>
                  {STATUS_LABEL[p.status]}
                </span>
              </td>
              <td>
                {p.warning_count > 0 ? (
                  <span className="badge badge-missing_checkout">⚠ {p.warning_count}</span>
                ) : (
                  <span className="muted small">—</span>
                )}
              </td>
              <td className="row-actions">
                <Link className="btn btn-ghost btn-sm" to={`/payroll/payslips/${p.id}`}>
                  View payslip
                </Link>
              </td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td colSpan={7} className="muted">No payslips yet.</td>
            </tr>
          )}
        </tbody>
      </table>
      {isMine && (
        <p className="muted small">
          You see your own payslips only — payroll roles see everyone&apos;s.
        </p>
      )}
    </div>
  );
}