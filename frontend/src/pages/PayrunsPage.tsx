import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";

import {
  ApiError,
  createPayrun,
  draftPayrunScope,
  listDepartments,
  listPayruns,
  listSalaryStructures,
} from "../api/client";
import type {
  DepartmentSummary,
  DraftScopeResponse,
  PayrunStatus,
  PayrunSummary,
  SalaryStructureSummary,
} from "../api/types";
import { fmtDate } from "../auth";

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

interface ScopeForm {
  salary_structure_id: string;
  period_start: string;
  period_end: string;
  department_filter_id: string;
  name: string;
}

const EMPTY_SCOPE: ScopeForm = {
  salary_structure_id: "",
  period_start: "",
  period_end: "",
  department_filter_id: "",
  name: "",
};

export function PayrunsPage() {
  const navigate = useNavigate();

  const [rows, setRows] = useState<PayrunSummary[]>([]);
  const [structures, setStructures] = useState<SalaryStructureSummary[]>([]);
  const [departments, setDepartments] = useState<DepartmentSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Wizard state (stateless - no row is created until step 2 finishes)
  const [wizardOpen, setWizardOpen] = useState(false);
  const [step, setStep] = useState<1 | 2>(1);
  const [scopeForm, setScopeForm] = useState<ScopeForm>(EMPTY_SCOPE);
  const [draft, setDraft] = useState<DraftScopeResponse | null>(null);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [stepSearch, setStepSearch] = useState("");
  const [stepDept, setStepDept] = useState("");

  const load = useCallback(async () => {
    setError(null);
    try {
      const [payrunsPage, structPage, deptPage] = await Promise.all([
        listPayruns(),
        listSalaryStructures(),
        listDepartments(),
      ]);
      setRows(payrunsPage.items);
      setStructures(structPage.items);
      setDepartments(deptPage.items);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load payruns.");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const openWizard = () => {
    setWizardOpen(true);
    setStep(1);
    setScopeForm(EMPTY_SCOPE);
    setDraft(null);
    setSelected(new Set());
    setStepSearch("");
    setStepDept("");
    setError(null);
    setNotice(null);
  };

  const closeWizard = () => {
    setWizardOpen(false);
    setDraft(null);
  };

  async function onStep1(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (!scopeForm.salary_structure_id || !scopeForm.period_start || !scopeForm.period_end) {
      setError("Salary structure and period are required.");
      return;
    }
    if (scopeForm.period_end < scopeForm.period_start) {
      setError("Period end must be on or after period start.");
      return;
    }
    setBusy(true);
    try {
      const res = await draftPayrunScope({
        salary_structure_id: Number(scopeForm.salary_structure_id),
        period_start: scopeForm.period_start,
        period_end: scopeForm.period_end,
        department_filter_id: scopeForm.department_filter_id
          ? Number(scopeForm.department_filter_id)
          : null,
        name: scopeForm.name || null,
      });
      setDraft(res);
      setSelected(new Set(res.eligible_employees.map((emp) => emp.id)));
      setStep(2);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load eligible employees.");
    } finally {
      setBusy(false);
    }
  }

  const visibleEligible = useMemo(() => {
    if (!draft) return [];
    const q = stepSearch.trim().toLowerCase();
    return draft.eligible_employees.filter((emp) => {
      if (stepDept && emp.department_name !== stepDept) return false;
      if (!q) return true;
      return (
        emp.full_name.toLowerCase().includes(q) ||
        emp.work_email.toLowerCase().includes(q) ||
        String(emp.id) === q
      );
    });
  }, [draft, stepSearch, stepDept]);

  function toggleEmp(id: number) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function onStep2(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (!draft) return;
    if (selected.size === 0) {
      setError("Select at least one employee.");
      return;
    }
    setBusy(true);
    try {
      const created = await createPayrun({
        scope: draft.scope,
        employee_ids: [...selected],
      });
      closeWizard();
      setNotice(`Payrun "${created.name}" created - open it to compute payslips.`);
      await load();
      navigate(`/payroll/payruns/${created.id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to create payrun.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="stack">
      <div className="row spread">
        <h2>Payruns</h2>
        <button className="btn btn-primary" onClick={openWizard} disabled={busy}>
          ＋ New payrun
        </button>
      </div>
      {error && <div className="alert alert-error">{error}</div>}
      {notice && <div className="alert alert-ok">{notice}</div>}

      {/* Wizard ------------------------------------------------------------ */}
      {wizardOpen && (
        <div className="wizard card">
          <div className="wizard-head">
            <h3>New payrun - step {step} of 2</h3>
            <button className="btn btn-ghost btn-sm" onClick={closeWizard} disabled={busy}>
              ✕ Close
            </button>
          </div>

          {step === 1 && (
            <form className="grid" onSubmit={onStep1}>
              <label className="field field-wide">
                <span>Salary structure</span>
                <select
                  value={scopeForm.salary_structure_id}
                  onChange={(e) =>
                    setScopeForm({ ...scopeForm, salary_structure_id: e.target.value })
                  }
                >
                  <option value="">Select…</option>
                  {structures.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.name} ({s.code})
                    </option>
                  ))}
                </select>
              </label>
              <label className="field">
                <span>Period start</span>
                <input
                  type="date"
                  value={scopeForm.period_start}
                  onChange={(e) => setScopeForm({ ...scopeForm, period_start: e.target.value })}
                />
              </label>
              <label className="field">
                <span>Period end</span>
                <input
                  type="date"
                  min={scopeForm.period_start}
                  value={scopeForm.period_end}
                  onChange={(e) => setScopeForm({ ...scopeForm, period_end: e.target.value })}
                />
              </label>
              <label className="field">
                <span>Department filter (optional)</span>
                <select
                  value={scopeForm.department_filter_id}
                  onChange={(e) =>
                    setScopeForm({ ...scopeForm, department_filter_id: e.target.value })
                  }
                >
                  <option value="">All departments</option>
                  {departments.map((d) => (
                    <option key={d.id} value={d.id}>{d.name}</option>
                  ))}
                </select>
              </label>
              <label className="field field-wide">
                <span>Name (optional)</span>
                <input
                  type="text"
                  value={scopeForm.name}
                  onChange={(e) => setScopeForm({ ...scopeForm, name: e.target.value })}
                  placeholder="e.g. Payrun - October 2026"
                />
              </label>
              <div className="row align-end">
                <button type="button" className="btn btn-ghost" onClick={closeWizard} disabled={busy}>
                  Cancel
                </button>
                <button className="btn btn-primary" disabled={busy}>
                  {busy ? "Loading employees…" : "Continue →"}
                </button>
              </div>
            </form>
          )}

          {step === 2 && draft && (
            <form onSubmit={onStep2}>
              <div className="grid" style={{ marginBottom: 12 }}>
                <label className="field field-wide">
                  <span>Search</span>
                  <input
                    type="text"
                    value={stepSearch}
                    onChange={(e) => setStepSearch(e.target.value)}
                    placeholder="Name or email…"
                  />
                </label>
                <label className="field">
                  <span>Department</span>
                  <select value={stepDept} onChange={(e) => setStepDept(e.target.value)}>
                    <option value="">All</option>
                    {[...new Set(draft.eligible_employees.map((e) => e.department_name))].map((d) => (
                      <option key={d} value={d}>{d}</option>
                    ))}
                  </select>
                </label>
              </div>

              <p className="muted small" style={{ marginBottom: 8 }}>
                {draft.eligible_employees.length} eligible employees ·{" "}
                <b>{selected.size}</b> selected · employees without a contract for the
                period are flagged.
              </p>

              <div className="wizard-list">
                {visibleEligible.map((emp) => (
                  <label key={emp.id} className="wizard-row">
                    <input
                      type="checkbox"
                      checked={selected.has(emp.id)}
                      onChange={() => toggleEmp(emp.id)}
                    />
                    <span className="ld-avatar" style={{ width: 30, height: 30, fontSize: 12 }}>
                      {emp.full_name.slice(0, 1)}
                    </span>
                    <span className="wizard-row-name">
                      <b>{emp.full_name}</b>
                      <span className="small muted">{emp.work_email}</span>
                    </span>
                    <span className="small muted">{emp.department_name}</span>
                    {!emp.has_contract ? (
                      <span className="badge badge-missing_checkout">No active contract</span>
                    ) : (
                      <span className="badge badge-ok">Has contract</span>
                    )}
                  </label>
                ))}
                {visibleEligible.length === 0 && (
                  <p className="muted">No employees match the filters.</p>
                )}
              </div>

              <div className="row" style={{ marginTop: 14 }}>
                <button
                  type="button"
                  className="btn btn-ghost"
                  onClick={() => setStep(1)}
                  disabled={busy}
                >
                  ← Back
                </button>
                <button className="btn btn-primary" disabled={busy || selected.size === 0}>
                  {busy ? "Creating…" : `Create payrun (${selected.size})`}
                </button>
              </div>
            </form>
          )}
        </div>
      )}

      {/* List --------------------------------------------------------------- */}
      <table className="table">
        <thead>
          <tr>
            <th>Name</th>
            <th>Structure</th>
            <th>Period</th>
            <th>Status</th>
            <th>Employees</th>
            <th>Payslips</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((p) => {
            const structure = structures.find((s) => s.id === p.salary_structure_id);
            return (
              <tr key={p.id}>
                <td><b>{p.name}</b></td>
                <td>{structure?.name ?? `#${p.salary_structure_id}`}</td>
                <td>{fmtDate(p.period_start)} → {fmtDate(p.period_end)}</td>
                <td>
                  <span className={`badge ${STATUS_CLASS[p.status]}`}>
                    {STATUS_LABEL[p.status]}
                  </span>
                </td>
                <td>{p.employee_count}</td>
                <td>{p.payslip_count}</td>
                <td className="row-actions">
                  <Link className="btn btn-ghost btn-sm" to={`/payroll/payruns/${p.id}`}>
                    Open
                  </Link>
                </td>
              </tr>
            );
          })}
          {rows.length === 0 && (
            <tr>
              <td colSpan={7} className="muted">No payruns yet - create your first one.</td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}