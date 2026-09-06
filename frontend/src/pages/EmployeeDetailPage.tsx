// Employee form view — breadcrumb header, EDIT toggle, avatar + identity
// block, Work Information two-column fields, and smart buttons that jump to
// the employee's Contracts / Attendance / Time Off filtered views.

import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import {
  ApiError,
  getEmployee,
  getEmployeeRelatedSummary,
  listDepartments,
  listJobPositions,
  listWorkingSchedules,
  updateEmployee,
} from "../api/client";
import type { EmployeeDetail as EmpDetail } from "../api/client";
import type {
  DepartmentSummary,
  JobPositionSummary,
  WorkingScheduleItem,
} from "../api/types";
import { PageHeader } from "../components/PageHeader";
import { SmartButton } from "../components/ui";
import { fmtDate, useAuth } from "../auth";

const TYPE_LABEL: Record<string, string> = {
  full_time: "Full-time",
  part_time: "Part-time",
  contract: "Contract",
  intern: "Intern",
};

function initials(name: string): string {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((p) => p[0]?.toUpperCase() ?? "")
    .join("");
}

interface EditForm {
  full_name: string;
  work_email: string;
  phone: string;
  department_id: string;
  job_position_id: string;
  manager_id: string;
  working_schedule_id: string;
  employee_type: string;
  status: string;
  date_of_joining: string;
  work_location: string;
}

function formFrom(d: EmpDetail): EditForm {
  return {
    full_name: d.full_name,
    work_email: d.work_email,
    phone: d.phone ?? "",
    department_id: String(d.department?.id ?? ""),
    job_position_id: String(d.job_position?.id ?? ""),
    manager_id: d.manager ? String(d.manager.id) : "",
    working_schedule_id: String(d.working_schedule?.id ?? ""),
    employee_type: d.employee_type,
    status: d.status,
    date_of_joining: d.date_of_joining,
    work_location: d.work_location ?? "",
  };
}

export function EmployeeDetailPage() {
  const { id } = useParams();
  const empId = Number(id);
  const nav = useNavigate();
  const { isHr } = useAuth();

  const [emp, setEmp] = useState<EmpDetail | null>(null);
  const [related, setRelated] = useState<{
    contracts_count: number;
    attendance_count: number;
    time_off_count: number;
    allocations_count: number;
  } | null>(null);
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [form, setForm] = useState<EditForm | null>(null);

  // Reference lists for the edit pickers.
  const [departments, setDepartments] = useState<DepartmentSummary[]>([]);
  const [positions, setPositions] = useState<JobPositionSummary[]>([]);
  const [schedules, setSchedules] = useState<WorkingScheduleItem[]>([]);

  const load = useCallback(async () => {
    if (!Number.isFinite(empId)) return;
    setError(null);
    try {
      const d = await getEmployee(empId);
      setEmp(d);
      setForm(formFrom(d));
      try {
        setRelated(await getEmployeeRelatedSummary(empId));
      } catch {
        setRelated(d.related ?? null);
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load employee.");
    }
  }, [empId]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!editing || !isHr) return;
    Promise.all([listDepartments(), listJobPositions(), listWorkingSchedules()])
      .then(([dp, jp, ws]) => {
        setDepartments(dp.items);
        setPositions(jp.items);
        setSchedules(ws.items);
      })
      .catch(() => {
        /* pickers stay empty; selects show current value only */
      });
  }, [editing, isHr]);

  async function onSave() {
    if (!form || !emp) return;
    setSaving(true);
    setError(null);
    setNotice(null);
    try {
      const payload: Record<string, unknown> = {
        full_name: form.full_name,
        work_email: form.work_email,
        phone: form.phone || null,
        department_id: form.department_id ? Number(form.department_id) : null,
        job_position_id: form.job_position_id ? Number(form.job_position_id) : null,
        manager_id: form.manager_id ? Number(form.manager_id) : null,
        working_schedule_id: form.working_schedule_id
          ? Number(form.working_schedule_id)
          : null,
        employee_type: form.employee_type,
        status: form.status,
        date_of_joining: form.date_of_joining,
        work_location: form.work_location || null,
      };
      await updateEmployee(emp.id, payload);
      setNotice("Employee updated.");
      setEditing(false);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to save employee.");
    } finally {
      setSaving(false);
    }
  }

  if (!emp && !error) return <div className="muted">Loading…</div>;

  if (error && !emp) {
    return (
      <div className="stack">
        <PageHeader title={`Employee / #${id}`} backTo="/employees" backLabel="Employees" />
        <div className="alert alert-error">{error}</div>
      </div>
    );
  }
  if (!emp) return null;

  const infoRows: [string, string][] = [
    ["Department", emp.department?.name ?? "—"],
    ["Job Position", emp.job_position?.title ?? "—"],
    ["Manager", emp.manager?.full_name ?? "—"],
    ["Working Schedule", emp.working_schedule?.name ?? "—"],
    ["Employee Type", TYPE_LABEL[emp.employee_type] ?? emp.employee_type],
    ["Work Email", emp.work_email],
    ["Phone", emp.phone ?? "—"],
    ["Work Location", emp.work_location ?? "—"],
    ["Date of Joining", fmtDate(emp.date_of_joining)],
    ["Company", emp.company_id ? `#${emp.company_id}` : "—"],
  ];

  return (
    <div className="stack">
      <PageHeader
        title={
          <>
            <span className="crumb">Employee / </span>
            {emp.full_name}
          </>
        }
        backTo="/employees"
        backLabel="Employees"
        actions={
          isHr && !editing ? (
            <button
              className="btn btn-ghost btn-sm"
              onClick={() => {
                setForm(formFrom(emp));
                setNotice(null);
                setEditing(true);
              }}
            >
              EDIT
            </button>
          ) : null
        }
      />

      {notice && <div className="alert alert-ok">{notice}</div>}
      {error && <div className="alert alert-error">{error}</div>}

      {/* Identity + smart buttons row ------------------------------------- */}
      <div className="card">
        <div className="row spread" style={{ flexWrap: "wrap", gap: 16 }}>
          <div className="row" style={{ gap: 12 }}>
            <span className="avatar avatar-lg">{initials(emp.full_name)}</span>
            <div>
              <div style={{ fontWeight: 600, fontSize: 16 }}>{emp.full_name}</div>
              <div className="muted small">
                {emp.job_position?.title ?? "—"} · {emp.department?.name ?? "—"}
              </div>
              <div className="muted small">
                {emp.work_email}
                {emp.phone ? ` · ${emp.phone}` : ""}
              </div>
            </div>
            <span className={`badge badge-${emp.status}`}>{emp.status}</span>
          </div>

          {/* Smart buttons — Contracts 2 · Attendance 14 · Time Off 3 */}
          <div className="row" style={{ gap: 8 }}>
            <SmartButton
              label="Contracts"
              count={related?.contracts_count ?? 0}
              to={`/contracts?employee=${emp.id}`}
            />
            <SmartButton
              label="Attendance"
              count={related?.attendance_count ?? 0}
              to={`/attendance?employee=${emp.id}`}
            />
            <SmartButton
              label="Time Off"
              count={related?.time_off_count ?? 0}
              to={`/time-off/requests?employee=${emp.id}`}
            />
          </div>
        </div>
      </div>

      {/* Form view / edit form --------------------------------------------- */}
      {editing && form ? (
        <div className="card">
          <h3>Edit employee</h3>
          <div className="detail-grid">
            <label className="field">
              <span>Full name</span>
              <input
                value={form.full_name}
                onChange={(e) => setForm({ ...form, full_name: e.target.value })}
              />
            </label>
            <label className="field">
              <span>Work email</span>
              <input
                type="email"
                value={form.work_email}
                onChange={(e) => setForm({ ...form, work_email: e.target.value })}
              />
            </label>
            <label className="field">
              <span>Phone</span>
              <input
                value={form.phone}
                onChange={(e) => setForm({ ...form, phone: e.target.value })}
              />
            </label>
            <label className="field">
              <span>Department</span>
              <select
                value={form.department_id}
                onChange={(e) => setForm({ ...form, department_id: e.target.value })}
              >
                <option value="">—</option>
                {departments.map((d) => (
                  <option key={d.id} value={d.id}>{d.name}</option>
                ))}
                {form.department_id &&
                  !departments.some((d) => String(d.id) === form.department_id) && (
                    <option value={form.department_id}>
                      Current (#{form.department_id})
                    </option>
                  )}
              </select>
            </label>
            <label className="field">
              <span>Job position</span>
              <select
                value={form.job_position_id}
                onChange={(e) => setForm({ ...form, job_position_id: e.target.value })}
              >
                <option value="">—</option>
                {positions.map((p) => (
                  <option key={p.id} value={p.id}>{p.title}</option>
                ))}
                {form.job_position_id &&
                  !positions.some((p) => String(p.id) === form.job_position_id) && (
                    <option value={form.job_position_id}>
                      Current (#{form.job_position_id})
                    </option>
                  )}
              </select>
            </label>
            <label className="field">
              <span>Working schedule</span>
              <select
                value={form.working_schedule_id}
                onChange={(e) =>
                  setForm({ ...form, working_schedule_id: e.target.value })
                }
              >
                <option value="">—</option>
                {schedules.map((s) => (
                  <option key={s.id} value={s.id}>{s.name}</option>
                ))}
                {form.working_schedule_id &&
                  !schedules.some((s) => String(s.id) === form.working_schedule_id) && (
                    <option value={form.working_schedule_id}>
                      Current (#{form.working_schedule_id})
                    </option>
                  )}
              </select>
            </label>
            <label className="field">
              <span>Employee type</span>
              <select
                value={form.employee_type}
                onChange={(e) => setForm({ ...form, employee_type: e.target.value })}
              >
                {["full_time", "part_time", "contract", "intern"].map((t) => (
                  <option key={t} value={t}>{TYPE_LABEL[t] ?? t}</option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>Status</span>
              <select
                value={form.status}
                onChange={(e) => setForm({ ...form, status: e.target.value })}
              >
                {["active", "inactive", "terminated"].map((s) => (
                  <option key={s} value={s}>{s}</option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>Date of joining</span>
              <input
                type="date"
                value={form.date_of_joining}
                onChange={(e) => setForm({ ...form, date_of_joining: e.target.value })}
              />
            </label>
            <label className="field">
              <span>Work location</span>
              <input
                value={form.work_location}
                onChange={(e) => setForm({ ...form, work_location: e.target.value })}
              />
            </label>
          </div>
          <div className="row" style={{ marginTop: 16 }}>
            <button className="btn btn-primary" disabled={saving} onClick={() => void onSave()}>
              {saving ? "Saving…" : "Save"}
            </button>
            <button className="btn btn-ghost" onClick={() => setEditing(false)}>
              Discard
            </button>
          </div>
        </div>
      ) : (
        <div className="card">
          <h3>Work Information</h3>
          <div className="detail-grid">
            {infoRows.map(([k, v]) => (
              <dl className="kv" key={k} style={{ margin: 0 }}>
                <dt>{k}</dt>
                <dd>{v}</dd>
              </dl>
            ))}
          </div>
        </div>
      )}

      {/* Related record shortcuts (bottom row) ----------------------------- */}
      <div className="row">
        <Link className="btn btn-ghost btn-sm" to={`/contracts?employee=${emp.id}`}>
          View contracts
        </Link>
        <Link className="btn btn-ghost btn-sm" to={`/attendance?employee=${emp.id}`}>
          View attendance
        </Link>
        <button
          className="btn btn-ghost btn-sm"
          onClick={() => nav(`/time-off/requests?employee=${emp.id}`)}
        >
          View time off
        </button>
      </div>
    </div>
  );
}
