// Payslip detail — full breakdown (lines + warnings) for payroll roles.
// EMPLOYEE self-service cannot read the detail endpoint (backend RBAC), so
// the page offers their PDF directly when they own the payslip.

import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import {
  ApiError,
  downloadPayslipPdf,
  getPayslip,
} from "../api/client";
import type { Payslip } from "../api/types";
import { fmtDate, fmtMoney, useAuth } from "../auth";
import { PayrunStatusBadge } from "./PayrunsPage";

const CATEGORY_LABEL: Record<string, string> = {
  basic: "Earning",
  allowance: "Allowance",
  deduction: "Deduction",
  gross: "Gross",
  contribution: "Contribution",
  net: "Net",
};

export function PayslipDetailPage() {
  const { payslipId } = useParams();
  const id = Number(payslipId);
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
      <div>
        <h2>Payslip #{id}</h2>
        <div className="card" style={{ padding: 16 }}>
          <p>
            Payslip details are available to payroll staff. As an employee you
            can download your own payslip PDF — the backend checks ownership.
          </p>
          <button className="btn btn-primary" disabled={downloading} onClick={() => void onPdf()}>
            {downloading ? "Downloading…" : "Download PDF"}
          </button>
          {error ? <div className="alert alert-error">{error}</div> : null}
          <p className="small muted" style={{ marginTop: 12 }}>
            <Link to="/payroll/payslips">← Back to my payslips</Link>
          </p>
        </div>
      </div>
    );
  }

  return (
    <div>
      <div className="row spread">
        <h2>
          <Link to="/payroll/payslips" className="muted" style={{ fontSize: 14 }}>
            ← Payslips
          </Link>{" "}
          Payslip #{id}
        </h2>
        {slip && <PayrunStatusBadge status={slip.status} />}
      </div>

      {error ? <div className="alert alert-error">{error}</div> : null}

      {!slip ? (
        <p className="muted">Loading…</p>
      ) : (
        <>
          <div className="kv" style={{ marginTop: 14 }}>
            <dt>Employee</dt>
            <dd>{slip.employee_name}</dd>
            <dt>Period</dt>
            <dd>
              {fmtDate(slip.period_start)} → {fmtDate(slip.period_end)}
            </dd>
            <dt>Worked days</dt>
            <dd>{slip.worked_days}</dd>
            <dt>Payrun</dt>
            <dd>
              <Link to={`/payroll/payruns/${slip.payrun_id}`}>
                Payrun #{slip.payrun_id}
              </Link>
            </dd>
            <dt>Net salary</dt>
            <dd>
              <b style={{ fontSize: 16 }}>{fmtMoney(slip.net_salary)}</b>
            </dd>
          </div>

          <div className="row-actions" style={{ margin: "12px 0" }}>
            <button className="btn btn-primary" disabled={downloading} onClick={() => void onPdf()}>
              {downloading ? "Downloading…" : "Download PDF"}
            </button>
          </div>

          <h3>Breakdown ({slip.lines.length} lines)</h3>
          <table className="table" style={{ marginTop: 6 }}>
            <thead>
              <tr>
                <th>#</th>
                <th>Code</th>
                <th>Name</th>
                <th>Type</th>
                <th style={{ textAlign: "right" }}>Amount</th>
              </tr>
            </thead>
            <tbody>
              {slip.lines.map((l) => (
                <tr key={l.id}>
                  <td className="muted">{l.sequence}</td>
                  <td>
                    <b>{l.code}</b>
                  </td>
                  <td>{l.name}</td>
                  <td>
                    <span className={`badge badge-${l.category}`}>
                      {CATEGORY_LABEL[l.category] ?? l.category}
                    </span>
                  </td>
                  <td style={{ textAlign: "right" }}>{fmtMoney(l.amount)}</td>
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr>
                <td colSpan={4}>
                  <b>Gross / Net</b>
                </td>
                <td style={{ textAlign: "right" }}>
                  {fmtMoney(slip.gross_salary)} /{" "}
                  <b>{fmtMoney(slip.net_salary)}</b>
                </td>
              </tr>
            </tfoot>
          </table>

          {slip.warnings.length > 0 && (
            <>
              <h3>Warnings ({slip.warnings.length})</h3>
              <ul className="stack">
                {slip.warnings.map((w) => (
                  <li key={w.id} className={`alert alert-${w.warning_type === "missing_contract" || w.warning_type === "negative_net" ? "error" : "ok"}`}>
                    <span className={`badge badge-warn-${w.warning_type}`}>
                      {w.warning_type.replaceAll("_", " ")}
                    </span>{" "}
                    {w.message}
                  </li>
                ))}
              </ul>
            </>
          )}
        </>
      )}
    </div>
  );
}
