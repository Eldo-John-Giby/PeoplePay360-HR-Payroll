import { useCallback, useEffect, useState, type FormEvent, type ReactNode } from "react";
import { useSearchParams } from "react-router-dom";
import "../styles.css"
import {
  ApiError,
  checkIn,
  checkOut,
  createManualAttendance,
  getAttendanceSummary,
  listAttendance,
  listMyAttendance,
  sweepMissingCheckouts,
  updateAttendance,
} from "../api/client";
import type { Attendance, AttendanceStatus, AttendanceSummary } from "../api/types";
import {
  fmtDateTime,
  fmtHours,
  fmtNum,
  naiveIso,
  toLocalInput,
  useAuth,
} from "../auth";

const STATUS_LABEL: Record<AttendanceStatus, string> = {
  present: "Present",
  late: "Late",
  absent: "Absent",
  overtime: "Overtime",
  missing_checkout: "Missing checkout",
};

const VIEW_TABLE = "table";
const VIEW_CARD = "card";

function StatusBadge({ status }: { status: AttendanceStatus }) {
  const cls =
    status === "present"
      ? "badge badge-ok"
      : status === "late"
      ? "badge badge-req-to_approve"
      : status === "absent"
      ? "badge badge-absent"
      : status === "overtime"
      ? "badge badge-present"
      : "badge badge-warn";

  return <span className={cls}>{STATUS_LABEL[status]}</span>;
}

function ErrorBanner({ message }: { message: string | null }) {
  if (!message) return null;
  return <div className="alert alert-error">{message}</div>;
}

function MetricCard({
  label,
  value,
  hint,
  icon,
}: {
  label: string;
  value: string | number;
  hint?: string;
  icon?: ReactNode;
}) {
  return (
    <div className="card metric" style={{ padding: 18, flex: 1, minWidth: 140, borderLeft: "4px solid var(--brand)" }}>
      <div className="row spread" style={{ marginBottom: 6 }}>
        {icon && <span style={{ color: "var(--brand)" }}>{icon}</span>}
        <span className="muted small" style={{ fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.5px" }}>
          {label}
        </span>
      </div>
      <b style={{ fontSize: 28, display: "block", color: "var(--brand)", fontWeight: 700, letterSpacing: "-0.5px" }}>{value}</b>
      {hint && <small className="muted" style={{ marginTop: 2, display: "block" }}>{hint}</small>}
    </div>
  );
}

function AttendanceCardView({
  rows,
  action,
}: {
  rows: Attendance[];
  action?: (row: Attendance) => ReactNode;
}) {
  return (
    <div className="cards" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(340px, 1fr))", gap: 14 }}>
      {rows.map((a) => (
        <div key={a.id} className="card" style={{ padding: 16 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 10 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <div className="user-avatar" style={{ width: 36, height: 36, fontSize: 14 }}>
                {(a.employee_name ?? `E${a.employee_id}`).charAt(0).toUpperCase()}
              </div>
              <div>
                <b style={{ color: "var(--ink)", fontSize: 14 }}>{a.employee_name ?? `Employee #${a.employee_id}`}</b>
                <div className="small muted" style={{ margin: 0 }}>ID #{a.employee_id}</div>
              </div>
            </div>
            <StatusBadge status={a.status} />
          </div>

          <div className="kv" style={{ margin: 0, gridTemplateColumns: "90px 1fr", gap: "2px 10px", fontSize: 13 }}>
            <dt className="muted" style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: "0.5px" }}>Date</dt>
            <dd style={{ fontSize: 13, fontWeight: 500 }}>{new Date(a.check_in).toLocaleDateString()}</dd>

            <dt className="muted" style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: "0.5px" }}>Check In</dt>
            <dd style={{ fontSize: 13 }}>{fmtDateTime(a.check_in)}</dd>

            <dt className="muted" style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: "0.5px" }}>Check Out</dt>
            <dd style={{ fontSize: 13 }}>
              {a.check_out ? (
                <span style={{ fontWeight: 500 }}>{fmtDateTime(a.check_out)}</span>
              ) : (
                <span className="badge badge-warn" style={{ fontSize: 11, padding: "1px 6px" }}>Active</span>
              )}
            </dd>

            <dt className="muted" style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: "0.5px" }}>Hours</dt>
            <dd style={{ fontSize: 13, fontWeight: 600, textAlign: "right" }}>{fmtHours(a.worked_hours)}</dd>
          </div>

          {a.is_manual_correction && (
            <div style={{ marginTop: 8, padding: "4px 8px", background: "var(--brand-tint)", borderRadius: 4, fontSize: 11, color: "var(--brand-dark)", display: "inline-block" }}>
              Manual correction
            </div>
          )}

          {action && (
            <div style={{ marginTop: 12, paddingTop: 10, borderTop: "1px solid var(--line)", display: "flex", gap: 8 }}>
              {action(a)}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

function AttendanceTable({
  rows,
  action,
  showNotes = false,
}: {
  rows: Attendance[];
  action?: (row: Attendance) => ReactNode;
  showNotes?: boolean;
}) {
  if (rows.length === 0) {
    return (
      <div className="card" style={{ padding: 24, textAlign: "center" }}>
        <p className="muted" style={{ margin: 0 }}>No attendance records found.</p>
      </div>
    );
  }

  return (
    <div className="card" style={{ padding: 0, overflow: "hidden" }}>
      <table className="table" style={{ margin: 0 }}>
        <thead>
          <tr>
            <th>Employee</th>
            <th>Date</th>
            <th>Check In</th>
            <th>Check Out</th>
            <th style={{ textAlign: "right" }}>Worked Hours</th>
            <th>Status</th>
            <th>Type</th>
            {showNotes && <th>Notes</th>}
            {action && <th style={{ textAlign: "right" }}>Actions</th>}
          </tr>
        </thead>
        <tbody>
          {rows.map((a) => (
            <tr key={a.id}>
              <td>
                <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                  <div className="user-avatar" style={{ width: 32, height: 32, fontSize: 13 }}>
                    {(a.employee_name ?? `E${a.employee_id}`).charAt(0).toUpperCase()}
                  </div>
                  <div>
                    <b style={{ color: "var(--ink)" }}>{a.employee_name ?? `Employee #${a.employee_id}`}</b>
                    <div className="small muted">ID #{a.employee_id}</div>
                  </div>
                </div>
              </td>
              <td>{new Date(a.check_in).toLocaleDateString()}</td>
              <td>
                <span style={{ fontWeight: 500 }}>{fmtDateTime(a.check_in)}</span>
              </td>
              <td>
                {a.check_out ? (
                  <span style={{ fontWeight: 500 }}>{fmtDateTime(a.check_out)}</span>
                ) : (
                  <span className="badge badge-warn">Active / Open</span>
                )}
              </td>
              <td style={{ textAlign: "right", fontWeight: 600 }}>{fmtHours(a.worked_hours)}</td>
              <td>
                <StatusBadge status={a.status} />
              </td>
              <td>
                {a.is_manual_correction ? (
                  <span className="badge badge-muted">Manual</span>
                ) : (
                  <span className="small muted">Auto punch</span>
                )}
              </td>
              {showNotes && <td className="small muted">{a.notes || "—"}</td>}
              {action && <td style={{ textAlign: "right" }}>{action(a)}</td>}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ---------------------------------------------------------------------------
// EMPLOYEE self-service: smart-button check-in/out + month summary
// ---------------------------------------------------------------------------

function EmployeeAttendance() {
  const { user } = useAuth();
  const [rows, setRows] = useState<Attendance[]>([]);
  const [summary, setSummary] = useState<AttendanceSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    try {
      const [list, sum] = await Promise.all([
        listMyAttendance(20),
        user?.employee ? getAttendanceSummary(user.employee.id) : Promise.resolve(null),
      ]);
      setRows(list.items);
      setSummary(sum);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load attendance.");
    }
  }, [user?.employee]);

  useEffect(() => {
    void load();
  }, [load]);

  const openRow = rows.find((a) => a.check_out === null) ?? null;

  async function onCheckIn() {
    setBusy(true);
    setError(null);
    try {
      await checkIn();
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Check-in failed.");
    } finally {
      setBusy(false);
    }
  }

  async function onCheckOut() {
    if (!openRow) return;
    setBusy(true);
    setError(null);
    try {
      await checkOut(openRow.id);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Check-out failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="stack" style={{ gap: 20 }}>
      <div className="row spread" style={{ alignItems: "center", marginBottom: 8 }}>
        <div>
          <h2 style={{ margin: 0, fontSize: 22, fontWeight: 700 }}>My Attendance</h2>
          <p className="muted small" style={{ margin: "4px 0 0" }}>
            Punch check-in / check-out and view your monthly attendance summary.
          </p>
        </div>
      </div>

      <ErrorBanner message={error} />

      {/* Punch Action Hero Card */}
      <div className="card" style={{ padding: 24, background: "#f8fafc" }}>
        <div className="row spread" style={{ alignItems: "center" }}>
          <div>
            <span className="small muted" style={{ fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.5px" }}>
              Current Status
            </span>
            <div style={{ marginTop: 4 }}>
              {openRow ? (
                <span className="badge badge-ok" style={{ fontSize: 15, padding: "6px 14px" }}>
                  Checked In (Since {fmtDateTime(openRow.check_in)})
                </span>
              ) : (
                <span className="badge badge-muted" style={{ fontSize: 15, padding: "6px 14px" }}>
                  Not Checked In
                </span>
              )}
            </div>
          </div>

          <div>
            {openRow ? (
              <button className="btn btn-warn" style={{ padding: "10px 24px", fontSize: 15 }} disabled={busy} onClick={onCheckOut}>
                {busy ? "Updating…" : "Check Out Now"}
              </button>
            ) : (
              <button className="btn btn-primary" style={{ padding: "10px 24px", fontSize: 15 }} disabled={busy} onClick={onCheckIn}>
                {busy ? "Updating…" : "Check In Now"}
              </button>
            )}
          </div>
        </div>
      </div>

      {summary && (
        <div className="cards" style={{ gap: 12 }}>
          <MetricCard label="Present Days" value={fmtNum(summary.present)} />
          <MetricCard label="Late Days" value={fmtNum(summary.late)} />
          <MetricCard label="Overtime" value={fmtNum(summary.overtime)} />
          <MetricCard label="Missing Checkout" value={fmtNum(summary.missing_checkout)} />
          <MetricCard label="Absent" value={fmtNum(summary.absent)} />
          <MetricCard label="Coverage" value={`${summary.coverage_pct.toFixed(1)}%`} hint="Current month" />
        </div>
      )}

      <div>
        <div className="row spread" style={{ marginBottom: 12 }}>
          <h3 style={{ margin: 0, fontSize: 16, fontWeight: 600 }}>
            Recent Attendance Records
          </h3>
          <div className="seg">
            <button
              type="button"
              className={VIEW_TABLE === VIEW_TABLE ? "on" : ""}
              onClick={() => setViewMode(VIEW_TABLE)}
            >
              Table
            </button>
            <button
              type="button"
              className={VIEW_CARD === VIEW_CARD ? "on" : ""}
              onClick={() => setViewMode(VIEW_CARD)}
            >
              Cards
            </button>
          </div>
        </div>
        {viewMode === VIEW_TABLE ? (
          <AttendanceTable rows={rows} showNotes />
        ) : (
          <AttendanceCardView rows={rows} />
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// HR: manual entry + correction editors
// ---------------------------------------------------------------------------

interface TimesForm {
  employee_id: string;
  check_in: string;
  check_out: string;
  notes: string;
}

const EMPTY_FORM: TimesForm = { employee_id: "", check_in: "", check_out: "", notes: "" };

function HrAttendance() {
  const [searchParams] = useSearchParams();
  const [rows, setRows] = useState<Attendance[]>([]);
  const [status, setStatus] = useState<AttendanceStatus | "">("");
  const [manualOnly, setManualOnly] = useState(false);
  const [employeeFilter, setEmployeeFilter] = useState(searchParams.get("employee") ?? "");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [viewMode, setViewMode] = useState<"table" | "card">(VIEW_TABLE);

  const [editing, setEditing] = useState<Attendance | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const raw = employeeFilter.trim();
      const empNum = raw === "" ? undefined : Number(raw);
      const page = await listAttendance({
        status: status || undefined,
        is_manual_correction: manualOnly || undefined,
        employee_id: empNum !== undefined && Number.isNaN(empNum) ? undefined : empNum,
        date_from: dateFrom || undefined,
        date_to: dateTo || undefined,
      });
      setRows(page.items);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load attendance.");
    }
  }, [status, manualOnly, employeeFilter, dateFrom, dateTo]);

  useEffect(() => {
    void load();
  }, [load]);

  async function onSweep() {
    setError(null);
    setNotice(null);
    try {
      const res = await sweepMissingCheckouts();
      setNotice(`Sweep completed successfully. ${res.swept} open entries marked as missing checkout.`);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Sweep failed.");
    }
  }

  const [form, setForm] = useState<TimesForm>(EMPTY_FORM);

  async function onManualCreate(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setNotice(null);
    const employee_id = Number(form.employee_id);
    if (!Number.isInteger(employee_id) || employee_id <= 0) {
      setError("Enter a valid employee ID (numeric).");
      return;
    }
    try {
      await createManualAttendance({
        employee_id,
        check_in: naiveIso(form.check_in),
        check_out: naiveIso(form.check_out),
        notes: form.notes || null,
      });
      setForm(EMPTY_FORM);
      setNotice("Manual attendance entry created successfully.");
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to create entry.");
    }
  }

  const [correction, setCorrection] = useState<TimesForm>(EMPTY_FORM);
  const [saving, setSaving] = useState(false);

  function startCorrection(row: Attendance) {
    setError(null);
    setNotice(null);
    setCorrection({
      employee_id: String(row.employee_id),
      check_in: toLocalInput(row.check_in),
      check_out: toLocalInput(row.check_out) || "",
      notes: row.notes ?? "",
    });
    setEditing(row);
  }

  async function saveCorrection(e: FormEvent) {
    e.preventDefault();
    if (!editing) return;
    setError(null);
    setNotice(null);
    setSaving(true);
    try {
      const changed: {
        check_in?: string;
        check_out?: string | null;
        notes?: string | null;
      } = {};
      if (correction.check_in) changed.check_in = naiveIso(correction.check_in);
      if (correction.check_out) changed.check_out = naiveIso(correction.check_out);
      if (correction.notes !== (editing.notes ?? "")) {
        changed.notes = correction.notes || null;
      }
      await updateAttendance(editing.id, changed);
      setEditing(null);
      setNotice(`Entry #${editing.id} corrected and recomputed successfully.`);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to save correction.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="stack" style={{ gap: 20 }}>
      {/* Header */}
      <div className="row spread" style={{ alignItems: "center", marginBottom: 8 }}>
        <div>
          <h2 style={{ margin: 0, fontSize: 22, fontWeight: 700 }}>Attendance — All Employees</h2>
          <p className="muted small" style={{ margin: "4px 0 0" }}>
            Monitor check-ins, run missing checkout sweeps, and apply manual corrections.
          </p>
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <div className="seg">
            <button
              type="button"
              className={viewMode === VIEW_TABLE ? "on" : ""}
              onClick={() => setViewMode(VIEW_TABLE)}
            >
              Table
            </button>
            <button
              type="button"
              className={viewMode === VIEW_CARD ? "on" : ""}
              onClick={() => setViewMode(VIEW_CARD)}
            >
              Cards
            </button>
          </div>
          <button className="btn btn-ghost" onClick={() => void onSweep()}>
            Run missing-checkout sweep
          </button>
        </div>
      </div>

      <ErrorBanner message={error} />
      {notice && <div className="alert alert-ok">{notice}</div>}

      {/* Filters Card */}
      <div className="card" style={{ padding: 14 }}>
        <div className="form-grid" style={{ marginBottom: 0 }}>
          <label className="field">
            <span>Filter Status</span>
            <select value={status} onChange={(e) => setStatus(e.target.value as AttendanceStatus | "")}>
              <option value="">All Statuses</option>
              {Object.entries(STATUS_LABEL).map(([v, label]) => (
                <option key={v} value={v}>
                  {label}
                </option>
              ))}
            </select>
          </label>

          <label className="field">
            <span>Date From</span>
            <input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} />
          </label>

          <label className="field">
            <span>Date To</span>
            <input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} />
          </label>

          <label className="field">
            <span>Search Employee ID</span>
            <input
              type="number"
              placeholder="Search employee ID…"
              value={employeeFilter}
              onChange={(e) => setEmployeeFilter(e.target.value)}
            />
          </label>

          <div className="field align-end">
            <label className="check" style={{ marginBottom: 8 }}>
              <input
                type="checkbox"
                checked={manualOnly}
                onChange={(e) => setManualOnly(e.target.checked)}
              />
              <span>Manual corrections only</span>
            </label>
          </div>

          {(status || manualOnly || employeeFilter || dateFrom || dateTo) && (
            <div className="row-actions" style={{ alignItems: "flex-end" }}>
              <button
                className="btn btn-ghost btn-sm"
                onClick={() => {
                  setStatus("");
                  setManualOnly(false);
                  setEmployeeFilter("");
                  setDateFrom("");
                  setDateTo("");
                }}
              >
                Reset Filters
              </button>
            </div>
          )}
        </div>
      </div>

      {/* Attendance Records */}
      {viewMode === VIEW_TABLE ? (
        <AttendanceTable
          rows={rows}
          showNotes
          action={(row) => (
            <button className="btn btn-ghost btn-sm" onClick={() => startCorrection(row)}>
              Correct
            </button>
          )}
        />
      ) : (
        <AttendanceCardView
          rows={rows}
          action={(row) => (
            <button className="btn btn-ghost btn-sm" onClick={() => startCorrection(row)}>
              Correct
            </button>
          )}
        />
      )}

      {/* Manual Entry or Correction Card */}
      {editing === null ? (
        <div className="card" style={{ padding: 20 }}>
          <h3 style={{ margin: "0 0 14px", fontSize: 16, fontWeight: 600 }}>Manual Attendance Entry</h3>
          <form onSubmit={onManualCreate} className="stack" style={{ gap: 14 }}>
            <div className="form-grid" style={{ marginBottom: 0 }}>
              <label className="field">
                <span>Employee ID</span>
                <input
                  type="number"
                  required
                  placeholder="e.g. 1"
                  value={form.employee_id}
                  onChange={(e) => setForm({ ...form, employee_id: e.target.value })}
                />
              </label>

              <label className="field">
                <span>Check In Time</span>
                <input
                  type="datetime-local"
                  required
                  value={form.check_in}
                  onChange={(e) => setForm({ ...form, check_in: e.target.value })}
                />
              </label>

              <label className="field">
                <span>Check Out Time</span>
                <input
                  type="datetime-local"
                  required
                  value={form.check_out}
                  onChange={(e) => setForm({ ...form, check_out: e.target.value })}
                />
              </label>

              <label className="field">
                <span>Notes (Optional)</span>
                <input
                  placeholder="Reason for manual entry…"
                  value={form.notes}
                  onChange={(e) => setForm({ ...form, notes: e.target.value })}
                />
              </label>

              <div className="row-actions" style={{ alignItems: "flex-end" }}>
                <button className="btn btn-primary" type="submit">
                  Add Entry
                </button>
              </div>
            </div>
          </form>
        </div>
      ) : (
        <div className="card" style={{ padding: 20 }}>
          <div className="row spread" style={{ marginBottom: 14, alignItems: "center" }}>
            <h3 style={{ margin: 0, fontSize: 16, fontWeight: 600 }}>
              Correct Entry #{editing.id} — {editing.employee_name ?? `Employee #${editing.employee_id}`}
            </h3>
            <div className="row" style={{ gap: 8, alignItems: "center" }}>
              <StatusBadge status={editing.status} />
              {editing.is_manual_correction && <span className="badge badge-muted">Previous correction</span>}
            </div>
          </div>

          <form onSubmit={saveCorrection} className="stack" style={{ gap: 14 }}>
            <div className="form-grid" style={{ marginBottom: 0 }}>
              <label className="field">
                <span>Check In Time</span>
                <input
                  type="datetime-local"
                  required
                  value={correction.check_in}
                  onChange={(e) => setCorrection({ ...correction, check_in: e.target.value })}
                />
              </label>

              <label className="field">
                <span>Check Out Time</span>
                <input
                  type="datetime-local"
                  value={correction.check_out}
                  onChange={(e) => setCorrection({ ...correction, check_out: e.target.value })}
                />
              </label>

              <label className="field">
                <span>Notes</span>
                <input
                  placeholder="Reason for correction…"
                  value={correction.notes}
                  onChange={(e) => setCorrection({ ...correction, notes: e.target.value })}
                />
              </label>

              <div className="row-actions" style={{ alignItems: "flex-end" }}>
                <button className="btn btn-primary" disabled={saving} type="submit">
                  {saving ? "Saving…" : "Save Correction"}
                </button>
                <button
                  className="btn btn-ghost"
                  type="button"
                  onClick={() => {
                    setEditing(null);
                    setCorrection(EMPTY_FORM);
                  }}
                >
                  Cancel
                </button>
              </div>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}

export function AttendancePage() {
  const { user, isHr } = useAuth();
  if (user === null) return <div className="muted" style={{ padding: 20 }}>Loading…</div>;

  if (!isHr) {
    if (user.employee === null) {
      return (
        <div className="alert alert-error">
          This account is not linked to an employee profile — HR must link it before attendance self-service works.
        </div>
      );
    }
    return <EmployeeAttendance />;
  }
  return <HrAttendance />;
}
