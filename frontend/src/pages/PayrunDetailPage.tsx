// Payrun detail — summary + the lifecycle action bar that drives the demo:
// compute -> validate -> mark-paid (and cancel / send-payslips where legal).
// The backend enforces the state machine (draft -> computed -> validated ->
// paid) plus blocking warnings; 409s surface as readable alerts here.

import { useCallback, useEffect, useState, type ReactNode } from "react";
import { Link, useParams } from "react-router-dom";

import {
  ApiError,
  cancelPayrun,
  computePayrun,
  getPayrun,
  markPayrunPaid,
  sendPayrunPayslips,
  validatePayrun,
} from "../api/client";
import type {
  CancelResult,
  ComputeResult,
  MarkPaidResult,
  Payrun,
  SendPayslipsResult,
  ValidateResult,
} from "../api/types";
import { fmtDate, fmtMoney, fmtNum, useAuth } from "../auth";
import { PayrunStatusBadge } from "./PayrunsPage";

type Action =
  | "compute"
  | "validate"
  | "mark-paid"
  | "cancel"
  | "send-payslips";

const ACTION_LABEL: Record<Action, string> = {
  compute: "Compute",
  validate: "Validate",
  "mark-paid": "Mark paid",
  cancel: "Cancel payrun",
  "send-payslips": "Send payslips",
};

function allowedActions(status: Payrun["status"]): Action[] {
  switch (status) {
    case "draft":
      return ["compute", "cancel"];
    case "computed":
      return ["compute", "validate", "cancel"];
    case "validated":
      return ["mark-paid", "send-payslips"];
    case "paid":
      return ["send-payslips"];
    default:
      return [];
  }
}

export function PayrunDetailPage() {
  const { payrunId } = useParams();
  const id = Number(payrunId);
  const { hasRole } = useAuth();
  const canRun =
    hasRole("HR_PAYROLL_USER", "HR_PAYROLL_MANAGER", "ADMIN") && Number.isFinite(id);

  const [payrun, setPayrun] = useState<Payrun | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState<Action | "">("");

  const load = useCallback(async () => {
    setError("");
    try {
      setPayrun(await getPayrun(id));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }, [id]);

  useEffect(() => {
    if (Number.isFinite(id)) void load();
  }, [id, load]);

  async function act(action: Action) {
    setBusy(action);
    setNotice("");
    setError("");
    try {
      const fn = {
        compute: computePayrun,
        validate: validatePayrun,
        "mark-paid": markPayrunPaid,
        cancel: cancelPayrun,
        "send-payslips": sendPayrunPayslips,
      }[action];
      const res: ComputeResult | ValidateResult | MarkPaidResult | CancelResult | SendPayslipsResult =
        await fn(id);
      const summary: ReactNode[] = [];
      if (action === "compute") {
        const r = res as ComputeResult;
        summary.push(`Computed ${r.payslips_computed} payslip(s)`);
        if (r.warnings_added > 0)
          summary.push(`, ${r.warnings_added} warning(s) added`);
        if (r.payslips_skipped.length)
          summary.push(
            ` (skipped finalized: ${r.payslips_skipped.map((s) => s.employee_name).join(", ")})`,
          );
      } else if (action === "validate") {
        const r = res as ValidateResult;
        summary.push(`Validated ${r.validated_payslips} payslip(s)`);
        if (r.blocking_warnings.length)
          summary.push(` — ${r.blocking_warnings.join("; ")}`);
      } else if (action === "mark-paid") {
        summary.push(`Marked ${(res as MarkPaidResult).paid_payslips} payslip(s) paid`);
      } else if (action === "cancel") {
        summary.push(`Cancelled ${(res as CancelResult).cancelled_payslips} payslip(s)`);
      } else {
        const r = res as SendPayslipsResult;
        summary.push(`Sent ${r.sent_count}, skipped ${r.skipped_count}`);
      }
      setNotice(summary.join(""));
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy("");
    }
  }

  const actions = payrun ? allowedActions(payrun.status) : [];

  return (
    <div>
      <div className="row spread">
        <h2>
          <Link to="/payroll/payruns" className="muted" style={{ fontSize: 14 }}>
            ← Payruns
          </Link>{" "}
          {payrun?.name ?? "Payrun"}
        </h2>
        {payrun && <PayrunStatusBadge status={payrun.status} />}
      </div>

      {error ? <div className="alert alert-error">{error}</div> : null}
      {notice ? <div className="alert alert-ok">{notice}</div> : null}

      {!payrun ? (
        <p className="muted">Loading…</p>
      ) : (
        <>
          {canRun && actions.length > 0 && (
            <div className="card" style={{ padding: 12, marginTop: 8 }}>
              <div className="row-actions">
                {actions.map((a) => (
                  <button
                    key={a}
                    className={`btn ${
                      a === "mark-paid"
                        ? "btn-ok"
                        : a === "cancel"
                          ? "btn-danger"
                          : "btn-primary"
                    }`}
                    disabled={busy !== ""}
                    onClick={() => void act(a)}
                  >
                    {busy === a ? "Working…" : ACTION_LABEL[a]}
                  </button>
                ))}
                <span className="muted small">
                  draft → computed → validated → paid; validated/paid runs are
                  history.
                </span>
              </div>
            </div>
          )}

          <div className="kv" style={{ marginTop: 16 }}>
            <dt>Period</dt>
            <dd>
              {fmtDate(payrun.period_start)} → {fmtDate(payrun.period_end)}
            </dd>
            <dt>Structure id</dt>
            <dd>{payrun.salary_structure_id}</dd>
            <dt>Version</dt>
            <dd>{payrun.version_id}</dd>
          </div>

          <h3 style={{ marginTop: 18 }}>Payslips ({fmtNum(payrun.payslips.length)})</h3>
          <table className="table" style={{ marginTop: 6 }}>
            <thead>
              <tr>
                <th>Employee</th>
                <th>Net salary</th>
                <th>Status</th>
                <th>Warnings</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {payrun.payslips.length === 0 ? (
                <tr>
                  <td colSpan={5} className="muted">
                    Not computed yet — click <b>Compute</b> to generate payslips.
                  </td>
                </tr>
              ) : (
                payrun.payslips.map((p) => (
                  <tr key={p.id}>
                    <td>{p.employee_name}</td>
                    <td>{fmtMoney(p.net_salary)}</td>
                    <td>
                      <PayrunStatusBadge status={p.status} />
                    </td>
                    <td>
                      {p.warning_count > 0 ? (
                        <span className="badge badge-req-to_approve">
                          {p.warning_count} warning{p.warning_count > 1 ? "s" : ""}
                        </span>
                      ) : (
                        <span className="muted">—</span>
                      )}
                    </td>
                    <td>
                      <Link className="btn btn-ghost btn-sm" to={`/payroll/payslips/${p.id}`}>
                        View
                      </Link>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}
