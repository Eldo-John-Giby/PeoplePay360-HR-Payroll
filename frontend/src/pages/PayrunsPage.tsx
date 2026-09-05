// Payruns — list + the 2-step wizard (scope -> pick employees -> create).
// The backend is stateless between steps: POST /payruns/draft-scope only
// returns eligible employees; the actual Payrun row is created by POST
// /payruns with the echoed scope + chosen employee ids.

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";

import {
  ApiError,
  createPayrun,
  draftPayrunScope,
  listPayruns,
  listSalaryStructures,
} from "../api/client";
import type {
  DraftScopeResponse,
  PayrunScope,
  PayrunStatus,
  PayrunSummary,
} from "../api/types";
import {
  fmtDate,
  fmtNum,
  monthEndIso,
  nextMonthStartIso,
  useAuth,
} from "../auth";

const STATUS_LABEL: Record<PayrunStatus, string> = {
  draft: "Draft",
  computed: "Computed",
  validated: "Validated",
  paid: "Paid",
  cancelled: "Cancelled",
};

export function PayrunStatusBadge({ status }: { status: PayrunStatus }) {
  return (
    <span className={`badge badge-payrun-${status}`}>
      {STATUS_LABEL[status]}
    </span>
  );
}

export function PayrunsPage() {
  const nav = useNavigate();
  const { hasRole } = useAuth();
  const canWrite = hasRole("HR_PAYROLL_USER", "HR_PAYROLL_MANAGER", "ADMIN");

  const [payruns, setPayruns] = useState<PayrunSummary[]>([]);
  const [structures, setStructures] = useState<{ id: number; name: string }[]>([]);
  const [statusFilter, setStatusFilter] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  // wizard state
  const [showWizard, setShowWizard] = useState(false);
  const [scope, setScope] = useState<PayrunScope>(() => {
    const start = nextMonthStartIso();
    return {
      salary_structure_id: 0,
      period_start: start,
      period_end: monthEndIso(start),
      name: "",
    };
  });
  const [preview, setPreview] = useState<DraftScopeResponse | null>(null);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [busy, setBusy] = useState(false);
  const [wizardMsg, setWizardMsg] = useState("");
  const [wizardErr, setWizardErr] = useState("");

  const load = useCallback(async () => {
    setError("");
    try {
      const page = await listPayruns(
        statusFilter ? { status: statusFilter as PayrunStatus } : {},
      );
      setPayruns(page.items);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [statusFilter]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!showWizard) return;
    listSalaryStructures()
      .then((p) => setStructures(p.items))
      .catch((err) =>
        setWizardErr(err instanceof ApiError ? err.message : String(err)),
      );
  }, [showWizard]);

  async function onSubmitScope(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setWizardErr("");
    setWizardMsg("");
    try {
      const res = await draftPayrunScope(scope);
      setPreview(res);
      setSelected(new Set());
    } catch (err) {
      setWizardErr(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  function toggleAll() {
    if (!preview) return;
    setSelected(
      selected.size === preview.eligible_employees.length
        ? new Set()
        : new Set(preview.eligible_employees.map((x) => x.id)),
    );
  }

  async function onCreate(e: FormEvent) {
    e.preventDefault();
    if (!preview) return;
    setBusy(true);
    setWizardErr("");
    setWizardMsg("");
    try {
      const run = await createPayrun({
        scope: preview.scope,
        employee_ids: [...selected],
      });
      resetWizard();
      nav(`/payroll/payruns/${run.id}`);
    } catch (err) {
      setWizardErr(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  function resetWizard() {
    setPreview(null);
    setSelected(new Set());
    setShowWizard(false);
    setWizardMsg("");
    setWizardErr("");
  }

  return (
    <div>
      <div className="row spread">
        <h2>Payruns</h2>
        <div className="row-actions">
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
          >
            <option value="">All statuses</option>
            {Object.entries(STATUS_LABEL).map(([v, l]) => (
              <option key={v} value={v}>
                {l}
              </option>
            ))}
          </select>
          {canWrite && !showWizard && (
            <button className="btn btn-primary" onClick={() => setShowWizard(true)}>
              New payrun
            </button>
          )}
          {showWizard && (
            <button className="btn btn-ghost" onClick={resetWizard}>
              Cancel
            </button>
          )}
        </div>
      </div>

      {error ? <div className="alert alert-error">{error}</div> : null}

      {showWizard && (
        <div className="card" style={{ marginTop: 12, padding: 16 }}>
          <h3 style={{ marginTop: 0 }}>
            {preview ? "Step 2 — pick employees" : "Step 1 — payrun scope"}
          </h3>
          {wizardMsg ? <div className="alert alert-ok">{wizardMsg}</div> : null}
          {wizardErr ? <div className="alert alert-error">{wizardErr}</div> : null}

          {!preview && (
            <form className="stack" onSubmit={onSubmitScope}>
              <div className="form-grid">
                <label className="field">
                  <span>Salary structure</span>
                  <select
                    required
                    value={scope.salary_structure_id}
                    onChange={(e) =>
                      setScope({ ...scope, salary_structure_id: Number(e.target.value) })
                    }
                  >
                    <option value={0} disabled>
                      Select…
                    </option>
                    {structures.map((s) => (
                      <option key={s.id} value={s.id}>
                        {s.name}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="field">
                  <span>Run name (optional)</span>
                  <input
                    value={scope.name ?? ""}
                    placeholder={`Payrun — ${scope.period_start.slice(0, 7)}`}
                    onChange={(e) => setScope({ ...scope, name: e.target.value })}
                  />
                </label>
                <label className="field">
                  <span>Period start</span>
                  <input
                    type="date"
                    required
                    value={scope.period_start}
                    onChange={(e) =>
                      setScope({ ...scope, period_start: e.target.value })
                    }
                  />
                </label>
                <label className="field">
                  <span>Period end</span>
                  <input
                    type="date"
                    required
                    value={scope.period_end}
                    onChange={(e) =>
                      setScope({ ...scope, period_end: e.target.value })
                    }
                  />
                </label>
                <label className="field">
                  <span>Department filter (optional)</span>
                  <input
                    type="number"
                    placeholder="department id"
                    value={scope.department_filter_id ?? ""}
                    onChange={(e) =>
                      setScope({
                        ...scope,
                        department_filter_id: e.target.value
                          ? Number(e.target.value)
                          : null,
                      })
                    }
                  />
                </label>
                <label className="field">
                  <span>Employee type filter (optional)</span>
                  <select
                    value={scope.employee_type_filter ?? ""}
                    onChange={(e) =>
                      setScope({
                        ...scope,
                        employee_type_filter: e.target.value || null,
                      })
                    }
                  >
                    <option value="">All types</option>
                    <option value="full_time">Full time</option>
                    <option value="part_time">Part time</option>
                    <option value="contract">Contract</option>
                    <option value="intern">Intern</option>
                  </select>
                </label>
              </div>
              <div className="row-actions">
                <button className="btn btn-primary" disabled={busy}>
                  Preview eligible employees
                </button>
              </div>
            </form>
          )}

          {preview && (
            <form className="stack" onSubmit={onCreate}>
              <p className="small muted">
                {fmtNum(preview.eligible_count)} active employee(s) match the
                scope. People without a contract covering the period are
                flagged — they will surface a{" "}
                <code>missing_contract</code> warning on Compute.
              </p>
              <div className="row-actions">
                <button type="button" className="btn btn-ghost btn-sm" onClick={toggleAll}>
                  {selected.size === preview.eligible_employees.length
                    ? "Clear all"
                    : "Select all"}
                </button>
                <span className="small muted">
                  {selected.size} selected
                </span>
              </div>
              <table className="table">
                <thead>
                  <tr>
                    <th style={{ width: 40 }} />
                    <th>Employee</th>
                    <th>Department</th>
                    <th>Type</th>
                    <th>Contract</th>
                  </tr>
                </thead>
                <tbody>
                  {preview.eligible_employees.map((emp) => (
                    <tr key={emp.id}>
                      <td>
                        <input
                          type="checkbox"
                          checked={selected.has(emp.id)}
                          onChange={() => {
                            const next = new Set(selected);
                            if (next.has(emp.id)) next.delete(emp.id);
                            else next.add(emp.id);
                            setSelected(next);
                          }}
                        />
                      </td>
                      <td>
                        <b>{emp.full_name}</b>
                        <div className="muted small">{emp.work_email}</div>
                      </td>
                      <td>{emp.department_name}</td>
                      <td>{emp.employee_type.replace("_", " ")}</td>
                      <td>
                        {emp.has_contract ? (
                          <span className="badge badge-ok">Covered</span>
                        ) : (
                          <span className="badge badge-payrun-cancelled">
                            No contract
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="row-actions">
                <button
                  type="button"
                  className="btn btn-ghost"
                  onClick={() => setPreview(null)}
                >
                  Back to scope
                </button>
                <button
                  className="btn btn-primary"
                  disabled={busy || selected.size === 0}
                >
                  Create payrun ({selected.size})
                </button>
              </div>
            </form>
          )}
        </div>
      )}

      {loading ? (
        <p className="muted">Loading…</p>
      ) : (
        <table className="table" style={{ marginTop: 12 }}>
          <thead>
            <tr>
              <th>Name</th>
              <th>Period</th>
              <th>Status</th>
              <th>Employees</th>
              <th>Payslips</th>
            </tr>
          </thead>
          <tbody>
            {payruns.length === 0 ? (
              <tr>
                <td colSpan={5} className="muted">
                  No payruns yet — click "New payrun" to create one.
                </td>
              </tr>
            ) : (
              payruns.map((p) => (
                <tr key={p.id}>
                  <td>
                    <Link to={`/payroll/payruns/${p.id}`}>{p.name}</Link>
                    <div className="muted small">
                      {fmtDate(p.period_start)} → {fmtDate(p.period_end)}
                    </div>
                  </td>
                  <td>{fmtDate(p.period_start)}</td>
                  <td>
                    <PayrunStatusBadge status={p.status} />
                  </td>
                  <td>{fmtNum(p.employee_count)}</td>
                  <td>{fmtNum(p.payslip_count)}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      )}
    </div>
  );
}
