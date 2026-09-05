import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";

import {
  ApiError,
  activateContract,
  cancelContract,
  createContract,
  expireContract,
  listContracts,
  listDepartments,
  listEmployees,
  listJobPositions,
  listMyContracts,
  listSalaryStructures,
  listWorkingSchedules,
  updateContract,
} from "../api/client";
import type {
  Contract,
  ContractStatus,
  DepartmentSummary,
  EmployeeListItem,
  JobPositionSummary,
  SalaryStructureSummary,
  WorkingScheduleItem,
} from "../api/types";
import { fmtDate, useAuth } from "../auth";

const STATUS_LABEL: Record<ContractStatus, string> = {
  draft: "Draft",
  running: "Active",
  expired: "Expired",
  cancelled: "Cancelled",
};

const STATUS_CLASS: Record<ContractStatus, string> = {
  draft: "badge-muted",
  running: "badge-ok",
  expired: "badge-alloc-refused",
  cancelled: "badge-req-cancelled",
};

export function fmtMoney(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") return "-";
  const n = Number(value);
  if (Number.isNaN(n)) return String(value);
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: 0,
  }).format(n);
}

/** Days between two ISO dates inclusive, 0 if invalid. */
function durationDays(from: string, to: string | null | undefined): number {
  if (!from || !to) return 0;
  const a = new Date(`${from}T00:00:00`);
  const b = new Date(`${to}T00:00:00`);
  if (Number.isNaN(a.getTime()) || Number.isNaN(b.getTime())) return 0;
  return Math.max(0, Math.round((b.getTime() - a.getTime()) / 86_400_000) + 1);
}

interface FormState {
  employee_id: string;
  department_id: string;
  job_position_id: string;
  working_schedule_id: string;
  salary_structure_id: string;
  wage_monthly: string;
  start_date: string;
  end_date: string;
}

const EMPTY_FORM: FormState = {
  employee_id: "",
  department_id: "",
  job_position_id: "",
  working_schedule_id: "",
  salary_structure_id: "",
  wage_monthly: "",
  start_date: "",
  end_date: "",
};

export function ContractsPage() {
  const { isHr } = useAuth();
  const [rows, setRows] = useState<Contract[]>([]);
  const [employees, setEmployees] = useState<EmployeeListItem[]>([]);
  const [departments, setDepartments] = useState<DepartmentSummary[]>([]);
  const [positions, setPositions] = useState<JobPositionSummary[]>([]);
  const [schedules, setSchedules] = useState<WorkingScheduleItem[]>([]);
  const [structures, setStructures] = useState<SalaryStructureSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Filters
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [deptFilter, setDeptFilter] = useState("");

  // Form
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [editing, setEditing] = useState<Contract | null>(null);
  const [overlapWarning, setOverlapWarning] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      if (!isHr) {
        setRows(await listMyContracts());
        return;
      }
      const [contractsPage, empPage, deptPage, posPage, schedPage, structPage] =
        await Promise.all([
          listContracts({ page_size: 100 } as never),
          listEmployees(),
          listDepartments(),
          listJobPositions(),
          listWorkingSchedules(),
          listSalaryStructures(),
        ]);
      setRows(contractsPage.items);
      setEmployees(empPage?.items ?? []);
      setDepartments(deptPage.items);
      setPositions(posPage.items);
      setSchedules(schedPage.items);
      setStructures(structPage.items);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load contracts.");
    }
  }, [isHr]);

  useEffect(() => {
    void load();
  }, [load]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return rows.filter((c) => {
      if (statusFilter && c.status !== statusFilter) return false;
      if (deptFilter && c.department?.id !== Number(deptFilter)) return false;
      if (!q) return true;
      const name = (c.employee?.full_name ?? "").toLowerCase();
      const num = c.contract_number.toLowerCase();
      return name.includes(q) || num.includes(q) || String(c.employee?.id) === q;
    });
  }, [rows, search, statusFilter, deptFilter]);

  const sameEmployeeRunning = useMemo(() => {
    if (!form.employee_id) return [];
    return rows.filter(
      (c) =>
        c.employee?.id === Number(form.employee_id) &&
        c.status === "running" &&
        (!editing || c.id !== editing.id),
    );
  }, [rows, form.employee_id, editing]);

  /** Overlap check vs other running contracts of the same employee. */
  function checkOverlap(state: FormState): string | null {
    const empId = Number(state.employee_id);
    if (!state.start_date || !empId) return null;
    const from = new Date(`${state.start_date}T00:00:00`);
    const to = state.end_date
      ? new Date(`${state.end_date}T00:00:00`)
      : new Date(8640000000000000);
    for (const c of sameEmployeeRunning) {
      const cFrom = new Date(`${c.start_date}T00:00:00`);
      const cTo = c.end_date
        ? new Date(`${c.end_date}T00:00:00`)
        : new Date(8640000000000000);
      const overlaps = from <= cTo && to >= cFrom;
      if (overlaps) {
        return `This overlaps an existing ${STATUS_LABEL[c.status]} contract for this employee (${c.contract_number}, ${fmtDate(c.start_date)} → ${c.end_date ? fmtDate(c.end_date) : "open"}).`;
      }
    }
    return null;
  }

  function onFormChange(patch: Partial<FormState>) {
    const next = { ...form, ...patch };
    setForm(next);
    setOverlapWarning(checkOverlap(next));
  }

  function startEdit(c: Contract) {
    setEditing(c);
    setOverlapWarning(null);
    setForm({
      employee_id: String(c.employee?.id ?? ""),
      department_id: String(c.department?.id ?? ""),
      job_position_id: String(c.job_position?.id ?? ""),
      working_schedule_id: String(c.working_schedule?.id ?? ""),
      salary_structure_id: String(c.salary_structure?.id ?? ""),
      wage_monthly: c.wage_monthly,
      start_date: c.start_date,
      end_date: c.end_date ?? "",
    });
  }

  function resetForm() {
    setEditing(null);
    setForm(EMPTY_FORM);
    setOverlapWarning(null);
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setNotice(null);
    const empId = Number(form.employee_id);
    if (!empId || !form.department_id || !form.job_position_id || !form.working_schedule_id || !form.salary_structure_id || !form.wage_monthly || !form.start_date) {
      setError("Employee, department, position, schedule, structure, wage and start date are required.");
      return;
    }
    if (form.end_date && form.end_date < form.start_date) {
      setError("End date cannot be before start date.");
      return;
    }
    setBusy(true);
    try {
      if (editing) {
        await updateContract(editing.id, {
          version_id: editing.version_id,
          department_id: Number(form.department_id) || null,
          job_position_id: Number(form.job_position_id) || null,
          working_schedule_id: Number(form.working_schedule_id) || null,
          salary_structure_id: Number(form.salary_structure_id) || null,
          wage_monthly: form.wage_monthly || null,
          start_date: form.start_date || null,
          end_date: form.end_date || null,
        });
        setNotice(`Contract ${editing.contract_number} updated.`);
      } else {
        const created = await createContract({
          employee_id: empId,
          department_id: Number(form.department_id),
          job_position_id: Number(form.job_position_id),
          working_schedule_id: Number(form.working_schedule_id),
          salary_structure_id: Number(form.salary_structure_id),
          wage_monthly: form.wage_monthly,
          start_date: form.start_date,
          end_date: form.end_date || null,
        });
        setNotice(
          `Contract ${created.contract_number} created as draft - activate it to make it current.`,
        );
      }
      resetForm();
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to save contract.");
    } finally {
      setBusy(false);
    }
  }

  async function act(id: number, action: "activate" | "expire" | "cancel") {
    const c = rows.find((r) => r.id === id);
    if (!c) return;
    setError(null);
    setNotice(null);
    setBusy(true);
    try {
      const verb = {
        activate: "activated",
        expire: "expired",
        cancel: "cancelled",
      }[action];
      if (action === "activate") await activateContract(id, c.version_id);
      if (action === "expire") await expireContract(id, c.version_id);
      if (action === "cancel") await cancelContract(id, c.version_id);
      setNotice(`Contract ${c.contract_number} ${verb}.`);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : `Failed to ${action} contract.`);
    } finally {
      setBusy(false);
    }
  }

  // Employee self-service: read-only own contracts.
  if (!isHr) {
    return (
      <div className="stack">
        <h2>My contracts</h2>
        {error && <div className="alert alert-error">{error}</div>}
        <table className="table">
          <thead>
            <tr>
              <th>Number</th>
              <th>Start</th>
              <th>End</th>
              <th>Wage</th>
              <th>Structure</th>
              <th>Department</th>
              <th>Position</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((c) => (
              <tr key={c.id}>
                <td>{c.contract_number}</td>
                <td>{fmtDate(c.start_date)}</td>
                <td>{c.end_date ? fmtDate(c.end_date) : "-"}</td>
                <td>{fmtMoney(c.wage_monthly)}</td>
                <td>{c.salary_structure?.name ?? "-"}</td>
                <td>{c.department?.name ?? "-"}</td>
                <td>{c.job_position?.title ?? "-"}</td>
                <td>
                  <span className={`badge ${STATUS_CLASS[c.status]}`}>
                    {STATUS_LABEL[c.status]}
                  </span>
                </td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr>
                <td colSpan={8} className="muted">No contracts on file.</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    );
  }

  return (
    <div className="stack">
      <div className="row spread">
        <h2>Contracts</h2>
        <button
          className="btn btn-ghost btn-sm"
          disabled={busy}
          onClick={() => {
            resetForm();
            setNotice(null);
          }}
        >
          {editing ? "Cancel edit" : "New contract"}
        </button>
      </div>
      {error && <div className="alert alert-error">{error}</div>}
      {notice && <div className="alert alert-ok">{notice}</div>}

      {(editing || form.employee_id || form.start_date) && (
        <div className="card">
          <h3>{editing ? `Edit ${editing.contract_number}` : "New contract"}</h3>
          {overlapWarning && <div className="alert alert-warn">{overlapWarning}</div>}
          <form className="grid" onSubmit={onSubmit}>
            <label className="field">
              <span>Employee</span>
              <select
                required
                value={form.employee_id}
                onChange={(e) => onFormChange({ employee_id: e.target.value })}
              >
                <option value="">Select…</option>
                {employees.map((e) => (
                  <option key={e.id} value={e.id}>
                    {e.full_name ?? e.name ?? `#${e.id}`}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>Start date</span>
              <input
                type="date"
                required
                value={form.start_date}
                onChange={(e) => onFormChange({ start_date: e.target.value })}
              />
            </label>
            <label className="field">
              <span>End date (blank = open-ended)</span>
              <input
                type="date"
                min={form.start_date}
                value={form.end_date}
                onChange={(e) => onFormChange({ end_date: e.target.value })}
              />
            </label>
            <label className="field">
              <span>Duration</span>
              <input
                type="text"
                value={
                  form.end_date
                    ? `${durationDays(form.start_date, form.end_date)} days`
                    : "Open-ended"
                }
                readOnly
                disabled
              />
            </label>
            <label className="field">
              <span>Department</span>
              <select
                value={form.department_id}
                onChange={(e) => onFormChange({ department_id: e.target.value })}
              >
                <option value="">Select…</option>
                {departments.map((d) => (
                  <option key={d.id} value={d.id}>{d.name}</option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>Position</span>
              <select
                value={form.job_position_id}
                onChange={(e) => onFormChange({ job_position_id: e.target.value })}
              >
                <option value="">Select…</option>
                {positions.map((p) => (
                  <option key={p.id} value={p.id}>{p.title}</option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>Monthly wage</span>
              <input
                type="number"
                min="0"
                step="0.01"
                required
                value={form.wage_monthly}
                onChange={(e) => onFormChange({ wage_monthly: e.target.value })}
                placeholder="e.g. 85000"
              />
            </label>
            <label className="field">
              <span>Working schedule</span>
              <select
                value={form.working_schedule_id}
                onChange={(e) => onFormChange({ working_schedule_id: e.target.value })}
              >
                <option value="">Select…</option>
                {schedules.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name} ({s.total_weekly_hours} h/wk)
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>Salary structure</span>
              <select
                value={form.salary_structure_id}
                onChange={(e) => onFormChange({ salary_structure_id: e.target.value })}
              >
                <option value="">Select…</option>
                {structures.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name} ({s.code})
                  </option>
                ))}
              </select>
            </label>
            <div className="field align-end">
              <button className="btn btn-primary" disabled={busy}>
                {busy ? "Saving…" : editing ? "Save changes" : "Create contract"}
              </button>
            </div>
          </form>
          <p className="muted small">
            New contracts are created as <em>draft</em>; use <strong>Activate</strong> on the
            row to make one current (it expires the employee&apos;s running contract).
          </p>
        </div>
      )}

      <div className="card">
        <div className="grid" style={{ marginBottom: 12 }}>
          <label className="field field-wide">
            <span>Search</span>
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Employee name or contract number…"
            />
          </label>
          <label className="field">
            <span>Status</span>
            <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
              <option value="">All</option>
              {Object.entries(STATUS_LABEL).map(([k, v]) => (
                <option key={k} value={k}>{v}</option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Department</span>
            <select value={deptFilter} onChange={(e) => setDeptFilter(e.target.value)}>
              <option value="">All</option>
              {departments.map((d) => (
                <option key={d.id} value={d.id}>{d.name}</option>
              ))}
            </select>
          </label>
        </div>

        <table className="table">
          <thead>
            <tr>
              <th>Employee</th>
              <th>Start</th>
              <th>End</th>
              <th>Wage</th>
              <th>Structure</th>
              <th>Department</th>
              <th>Position</th>
              <th>Status</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((c) => (
              <tr key={c.id} className={c.status === "running" ? "row-selected" : undefined}>
                <td>
                  <b>{c.employee?.full_name ?? `#${c.employee?.id ?? c.id}`}</b>
                  <div className="small muted">{c.contract_number}</div>
                </td>
                <td>{fmtDate(c.start_date)}</td>
                <td>{c.end_date ? fmtDate(c.end_date) : "-"}</td>
                <td>{fmtMoney(c.wage_monthly)}</td>
                <td>{c.salary_structure?.name ?? "-"}</td>
                <td>{c.department?.name ?? "-"}</td>
                <td>{c.job_position?.title ?? "-"}</td>
                <td>
                  <span className={`badge ${STATUS_CLASS[c.status]}`}>
                    {STATUS_LABEL[c.status]}
                  </span>
                </td>
                <td className="row-actions">
                  {c.status === "draft" && (
                    <>
                      <button
                        className="btn btn-ok btn-sm"
                        disabled={busy}
                        onClick={() => void act(c.id, "activate")}
                        title="Make this the current contract (expires the running one)"
                      >
                        Activate
                      </button>
                      <button
                        className="btn btn-ghost btn-sm"
                        disabled={busy}
                        onClick={() => startEdit(c)}
                      >
                        Edit
                      </button>
                      <button
                        className="btn btn-danger btn-sm"
                        disabled={busy}
                        onClick={() => void act(c.id, "cancel")}
                      >
                        Cancel
                      </button>
                    </>
                  )}
                  {c.status === "running" && (
                    <button
                      className="btn btn-warn btn-sm"
                      disabled={busy}
                      onClick={() => void act(c.id, "expire")}
                      title="Expire this contract now"
                    >
                      Expire
                    </button>
                  )}
                </td>
              </tr>
            ))}
            {filtered.length === 0 && (
              <tr>
                <td colSpan={9} className="muted">No contracts match the filters.</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}