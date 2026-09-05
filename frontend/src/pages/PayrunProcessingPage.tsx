import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";

import {
  ApiError,
  cancelPayrun,
  computePayrun,
  getPayrun,
  listPayslips,
  listSalaryStructures,
  markPayrunPaid,
  sendPayslips,
  validatePayrun,
} from "../api/client";
import type {
  PayrunDetail,
  PayrunStatus,
  PayslipSummaryItem,
  PayslipWarning,
  SalaryStructureSummary,
} from "../api/types";
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

const WARNING_LABEL: Record<string, string> = {
  missing_bank_details: "Missing bank details",
  duplicate_payslip: "Duplicate payslip detected",
  missing_contract: "No active contract",
  negative_net: "Negative net salary",
  overlapping_period: "Overlapping period",
  other: "Other",
};

export function PayrunProcessingPage() {
  const { id } = useParams();
  const payrunId = Number(id);
  const { hasRole } = useAuth();
  const canManage = hasRole("HR_PAYROLL_MANAGER", "ADMIN", "HR_PAYROLL_USER");

  const [run, setRun] = useState<PayrunDetail | null>(null);
  const [slips, setSlips] = useState<PayslipSummaryItem[]>([]);
  const [structures, setStructures] = useState<SalaryStructureSummary[]>([]);
  const [warnings, setWarnings] = useState<
    { employee_name: string; message: string; warning_type: string }[]
  >([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null); // which action is running

  const load = useCallback(async () => {
    setError(null);
    try {
      const [runData, slipPage, structPage] = await Promise.all([
        getPayrun(payrunId),
        listPayslips({ payrun_id: payrunId }),
        listSalaryStructures(),
      ]);
      setRun(runData);
      setSlips(slipPage.items);
      setStructures(structPage.items);
      // Fetch warnings for any payslip flagged with warning_count > 0.
      const flagged = slipPage.items.filter((s) => s.warning_count > 0);
      const warns: {
        employee_name: string;
        message: string;
        warning_type: string;
      }[] = [];
      for (const s of flagged) {
        try {
          const res = await fetch(`/api/v1/payroll/payslips/${s.id}`, {
            headers: { Authorization: `Bearer ${localStorage.getItem("pp360.access_token")}` },
          });
          if (res.ok) {
            const detail = (await res.json()) as { warnings?: PayslipWarning[] };
            for (const w of detail.warnings ?? []) {
              warns.push({
                employee_name: s.employee_name,
                message: `${WARNING_LABEL[w.warning_type] ?? w.warning_type}: ${w.message}`,
                warning_type: w.warning_type,
              });
            }
          }
        } catch {
          /* ignore per-payslip warning fetch errors */
        }
      }
      setWarnings(warns);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load payrun.");
    }
  }, [payrunId]);

  useEffect(() => {
    void load();
  }, [load]);

  const structure = useMemo(
    () => structures.find((s) => s.id === run?.salary_structure_id),
    [structures, run],
  );

  const totals = useMemo(
    () => ({
      gross: slips.reduce((acc, s) => acc + Number(s.gross_salary), 0),
      net: slips.reduce((acc, s) => acc + Number(s.net_salary), 0),
    }),
    [slips],
  );

  const status = run?.status ?? "draft";
  const isLocked = status === "paid" || status === "cancelled";

  /** Warnings that block Validate (mirrors the backend rule set). */
  const blockingCount = warnings.filter(
    (w) => w.warning_type === "missing_contract" || w.warning_type === "negative_net",
  ).length;

  const stepDone = {
    computed: status === "computed" || status === "validated" || status === "paid",
    validated: status === "validated" || status === "paid",
    paid: status === "paid",
    sent: status === "paid",
  };

  async function act(action: "compute" | "validate" | "mark-paid" | "send" | "cancel") {
    setError(null);
    setNotice(null);
    setBusy(action);
    try {
      if (action === "compute") {
        const res = await computePayrun(payrunId);
        setNotice(
          `Compute finished: ${res.payslips_computed} payslip(s) generated, ` +
            `${res.payslips_skipped.length} skipped, ${res.warnings_added} warning(s) added.`,
        );
      } else if (action === "validate") {
        const res = await validatePayrun(payrunId);
        setNotice(`Validated ${res.validated_payslips} payslip(s).`);
      } else if (action === "mark-paid") {
        const res = await markPayrunPaid(payrunId);
        setNotice(`${res.paid_payslips} payslip(s) marked paid. The run is now locked.`);
      } else if (action === "send") {
        const res = await sendPayslips(payrunId);
        setNotice(
          `${res.sent_count} payslip(s) emailed successfully` +
            (res.skipped_count ? ` (${res.skipped_count} skipped)` : "") +
            ".",
        );
      } else {
        const res = await cancelPayrun(payrunId);
        setNotice(`Payrun cancelled (${res.cancelled_payslips} payslip(s) removed).`);
      }
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : `Action failed.`);
    } finally {
      setBusy(null);
    }
  }

  if (!run) {
    return (
      <div className="stack">
        <h2>Payrun</h2>
        {error && <div className="alert alert-error">{error}</div>}
        <div className="muted">Loading…</div>
      </div>
    );
  }

  return (
    <div className="stack">
      <div className="row spread">
        <h2>{run.name}</h2>
        <Link className="btn btn-ghost btn-sm" to="/payroll/payruns">
          ← All payruns
        </Link>
      </div>
      {error && <div className="alert alert-error">{error}</div>}
      {notice && <div className="alert alert-ok">{notice}</div>}

      {/* Header ------------------------------------------------------------ */}
      <div className="card">
        <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))" }}>
          <div>
            <div className="small muted">Salary structure</div>
            <b>{structure?.name ?? `#${run.salary_structure_id}`}</b>
          </div>
          <div>
            <div className="small muted">Period</div>
            <b>{fmtDate(run.period_start)} → {fmtDate(run.period_end)}</b>
          </div>
          <div>
            <div className="small muted">Status</div>
            <span className={`badge ${STATUS_CLASS[status]}`}>{STATUS_LABEL[status]}</span>
          </div>
          <div>
            <div className="small muted">Employees</div>
            <b>{slips.length}</b>
          </div>
        </div>
      </div>

      {/* Action bar ---------------------------------------------------------- */}
      <div className="card">
        <h3 style={{ fontSize: 13 }}>Run the payroll</h3>
        <div className="row">
          <button
            className="btn btn-primary"
            disabled={!canManage || isLocked || busy !== null}
            onClick={() => void act("compute")}
            title="Generate payslips for every selected employee"
          >
            {busy === "compute" ? "Computing…" : stepDone.computed ? "✓ Recompute" : "Compute"}
          </button>
          <button
            className="btn btn-warn"
            disabled={
              !canManage ||
              isLocked ||
              !stepDone.computed ||
              blockingCount > 0 ||
              busy !== null
            }
            onClick={() => void act("validate")}
            title={
              blockingCount > 0
                ? `Blocked by ${blockingCount} blocking warning(s) — resolve them first`
                : "Validate payslips (blocked while blocking warnings are open)"
            }
          >
            {busy === "validate" ? "Validating…" : stepDone.validated ? "✓ Validated" : "Validate"}
          </button>
          <button
            className="btn btn-ok"
            disabled={!canManage || isLocked || !stepDone.validated || busy !== null}
            onClick={() => void act("mark-paid")}
            title="Lock the run and mark all payslips paid"
          >
            {busy === "mark-paid" ? "Marking…" : stepDone.paid ? "✓ Paid" : "Mark paid"}
          </button>
          <button
            className="btn btn-ghost"
            disabled={!canManage || isLocked || !stepDone.paid || busy !== null}
            onClick={() => void act("send")}
            title="Email payslips to employees (simulated)"
          >
            {busy === "send" ? "Sending…" : "Send payslips"}
          </button>
          {!isLocked && (
            <button
              className="btn btn-danger"
              disabled={busy !== null}
              onClick={() => void act("cancel")}
            >
              Cancel run
            </button>
          )}
        </div>

        {isLocked && (
          <p className="muted small" style={{ marginTop: 8 }}>
            🔒 This run is {status === "paid" ? "paid" : "cancelled"} and locked — it&apos;s a
            historical record. No further edits are allowed.
          </p>
        )}
      </div>

      {/* Warnings panel ------------------------------------------------------- */}
      {warnings.length > 0 && (
        <div className="card warn-panel">
          <h3 style={{ fontSize: 13 }}>
            ⚠️ Warnings — {warnings.length} open on this run
          </h3>
          <ul className="warn-list">
            {warnings.map((w, i) => (
              <li key={i}>
                <b>{w.employee_name}</b> — {w.message}
              </li>
            ))}
          </ul>
          {blockingCount > 0 && (
            <p className="small" style={{ marginTop: 8 }}>
              <b>{blockingCount}</b> blocking warning(s) open (missing contract / negative
              net) — <b>Validate</b> stays disabled until they&apos;re resolved.
            </p>
          )}
        </div>
      )}

      {/* Payslip summary table ------------------------------------------------ */}
      <div className="card">
        <div className="row spread" style={{ marginBottom: 10 }}>
          <h3 style={{ fontSize: 14 }}>Payslips</h3>
          {slips.length > 0 && (
            <span className="small muted">
              Gross total: <b>{fmtMoney(totals.gross.toFixed(2))}</b> · Net total:{" "}
              <b>{fmtMoney(totals.net.toFixed(2))}</b>
            </span>
          )}
        </div>
        <table className="table">
          <thead>
            <tr>
              <th>Employee</th>
              <th>Worked days</th>
              <th>Gross</th>
              <th>Net</th>
              <th>Status</th>
              <th>Warnings</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {slips.map((s) => (
              <tr key={s.id}>
                <td><b>{s.employee_name}</b></td>
                <td>—</td>
                <td>{fmtMoney(s.gross_salary)}</td>
                <td><b>{fmtMoney(s.net_salary)}</b></td>
                <td>
                  <span className={`badge ${STATUS_CLASS[s.status]}`}>
                    {STATUS_LABEL[s.status]}
                  </span>
                </td>
                <td>
                  {s.warning_count > 0 ? (
                    <span className="badge badge-missing_checkout">⚠ {s.warning_count}</span>
                  ) : (
                    <span className="muted small">—</span>
                  )}
                </td>
                <td className="row-actions">
                  <Link className="btn btn-ghost btn-sm" to={`/payroll/payslips/${s.id}`}>
                    View
                  </Link>
                </td>
              </tr>
            ))}
            {slips.length === 0 && (
              <tr>
                <td colSpan={7} className="muted">
                  No payslips yet — press <b>Compute</b> to generate them.
                </td>
              </tr>
            )}
          </tbody>
        </table>
        <p className="muted small" style={{ marginTop: 8 }}>
          Worked days come from attendance + approved time off for the period — visible on
          each payslip&apos;s detail page.
        </p>
      </div>
    </div>
  );
}