import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { ApiError, getPayslip } from "../api/client";
import type { PayslipDetail, SalaryRuleCategory } from "../api/types";
import { fmtDate, useAuth } from "../auth";
import { fmtMoney } from "./ContractsPage";

const CATEGORY_LABEL: Record<SalaryRuleCategory, string> = {
  basic: "Basic",
  allowance: "Allowance",
  deduction: "Deduction",
  gross: "Gross",
  contribution: "Contribution",
  net: "Net",
};

const STATUS_LABEL = {
  draft: "Draft",
  computed: "Computed",
  validated: "Validated",
  paid: "Paid",
  cancelled: "Cancelled",
} as const;

export function PayslipDetailPage() {
  const { id } = useParams();
  const payslipId = Number(id);
  const { user } = useAuth();
  const [slip, setSlip] = useState<PayslipDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      setSlip(await getPayslip(payslipId));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load payslip.");
    }
  }, [payslipId]);

  useEffect(() => {
    void load();
  }, [load]);

  const { grossLines, netLines } = useMemo(() => {
    if (!slip) return { grossLines: [], netLines: [] };
    const lines = [...slip.lines].sort((a, b) => a.sequence - b.sequence);
    const gross = lines.filter(
      (l) => l.category === "basic" || l.category === "allowance" || l.category === "gross" || l.category === "contribution",
    );
    const net = lines.filter((l) => l.category === "deduction" || l.category === "net");
    return { grossLines: gross, netLines: net };
  }, [slip]);

  if (error) {
    return (
      <div className="stack">
        <h2>Payslip</h2>
        <div className="alert alert-error">{error}</div>
        <Link className="btn btn-ghost btn-sm" to="/payroll/payslips">← Back</Link>
      </div>
    );
  }

  if (!slip) {
    return (
      <div className="stack">
        <h2>Payslip</h2>
        <div className="muted">Loading…</div>
      </div>
    );
  }

  const isOwner = user?.employee_id === slip.employee_id;

  return (
    <div className="stack">
      <div className="row spread">
        <h2>Payslip - {slip.employee_name}</h2>
        <div className="row">
          <button className="btn btn-ghost" onClick={() => window.print()}>
            🖨 Print payslip
          </button>
          <Link className="btn btn-ghost btn-sm" to="/payroll/payslips">
            ← All payslips
          </Link>
        </div>
      </div>
      {error && <div className="alert alert-error">{error}</div>}

      <div className="payslip-print">
        <div className="card">
          <div className="row spread" style={{ marginBottom: 14 }}>
            <div>
              <b style={{ fontSize: 18 }}>PeoplePay360</b>
              <div className="small muted">Salary payslip · {slip.employee_name}</div>
            </div>
            <span className={`badge ${slip.status === "paid" ? "badge-ok" : "badge-muted"}`}>
              {STATUS_LABEL[slip.status]}
            </span>
          </div>

          <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))" }}>
            <div>
              <div className="small muted">Employee</div>
              <b>{slip.employee_name}</b>
            </div>
            <div>
              <div className="small muted">Period</div>
              <b>{fmtDate(slip.period_start)} → {fmtDate(slip.period_end)}</b>
            </div>
            <div>
              <div className="small muted">Worked days</div>
              <b>{fmtMoney(slip.worked_days)}</b>
            </div>
            <div>
              <div className="small muted">Payrun</div>
              <b>#{slip.payrun_id}</b>
            </div>
          </div>

          <h3 style={{ margin: "20px 0 8px", fontSize: 14 }}>Salary computation</h3>
          <table className="table">
            <thead>
              <tr>
                <th>Rule</th>
                <th>Category</th>
                <th style={{ textAlign: "right" }}>Amount</th>
              </tr>
            </thead>
            <tbody>
              {grossLines.map((l) => (
                <tr key={l.id}>
                  <td><b>{l.name}</b><div className="small muted">{l.code}</div></td>
                  <td>
                    <span className={`badge badge-cat-${l.category}`}>
                      {CATEGORY_LABEL[l.category]}
                    </span>
                  </td>
                  <td style={{ textAlign: "right" }}>{fmtMoney(l.amount)}</td>
                </tr>
              ))}
              <tr>
                <td colSpan={2}><b>Gross salary</b></td>
                <td style={{ textAlign: "right" }}><b>{fmtMoney(slip.gross_salary)}</b></td>
              </tr>
              {netLines.map((l) => (
                <tr key={l.id}>
                  <td>{l.name}<div className="small muted">{l.code}</div></td>
                  <td>
                    <span className={`badge badge-cat-${l.category}`}>
                      {CATEGORY_LABEL[l.category]}
                    </span>
                  </td>
                  <td style={{ textAlign: "right" }}>{fmtMoney(l.amount)}</td>
                </tr>
              ))}
              <tr>
                <td colSpan={2}><b>Net salary</b></td>
                <td style={{ textAlign: "right" }}>
                  <b style={{ fontSize: 16 }}>{fmtMoney(slip.net_salary)}</b>
                </td>
              </tr>
            </tbody>
          </table>

          {slip.warnings.length > 0 && (
            <div className="warn-panel" style={{ marginTop: 14 }}>
              <h4 style={{ fontSize: 13, margin: "0 0 6px" }}>⚠ Warnings</h4>
              <ul className="warn-list">
                {slip.warnings.map((w) => (
                  <li key={w.id}>{w.message}</li>
                ))}
              </ul>
            </div>
          )}

          <p className="muted small" style={{ marginTop: 16 }}>
            Generated {fmtDate(slip.created_at)} · Payslip #{slip.id} ·{" "}
            {isOwner ? "you are the payslip owner" : "viewed by payroll role"}
          </p>
        </div>
      </div>
    </div>
  );
}