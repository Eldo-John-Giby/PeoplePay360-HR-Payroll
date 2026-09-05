import { useCallback, useEffect, useState, type FormEvent, type ReactNode } from "react";
import { useSearchParams } from "react-router-dom";

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

function StatusBadge({ status }: { status: AttendanceStatus }) {
  return <span className={`badge badge-${status}`}>{STATUS_LABEL[status]}</span>;
}

function ErrorBanner({ message }: { message: string | null }) {
  if (!message) return null;
  return <div className="alert alert-error">{message}</div>;
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
  if (rows.length === 0) return <div className="muted">No attendance records.</div>;
  return (
    <table className="table">
      <thead>
        <tr>
          <th>Employee</th>
          <th>Date</th>
          <th>Check in</th>
          <th>Check out</th>
          <th>Hours</th>
          <th>Status</th>
          <th>Manual</th>
          {showNotes && <th>Notes</th>}
          {action && <th></th>}
        </tr>
      </thead>
      <tbody>
        {rows.map((a) => (
          <tr key={a.id}>
            <td>{a.employee_name ?? `#${a.employee_id}`}</td>
            <td>{new Date(a.check_in).toLocaleDateString()}</td>
            <td>{fmtDateTime(a.check_in)}</td>
            <td>{fmtDateTime(a.check_out)}</td>
            <td>{fmtHours(a.worked_hours)}</td>
            <td><StatusBadge status={a.status} /></td>
            <td>{a.is_manual_correction ? "✎" : ""}</td>
            {showNotes && <td>{a.notes || ""}</td>}
            {action && <td>{action(a)}</td>}
          </tr>
        ))}
      </tbody>
    </table>
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
    <div className="stack">
      <h2>My attendance</h2>
      <ErrorBanner message={error} />

      <div className="row">
        {openRow ? (
          <button className="btn btn-warn" disabled={busy} onClick={onCheckOut}>
            {busy ? "Working…" : "Check out"}
            {openRow.check_in && <> (in since {fmtDateTime(openRow.check_in)})</>}
          </button>
        ) : (
          <button className="btn btn-primary" disabled={busy} onClick={onCheckIn}>
            {busy ? "Working…" : "Check in now"}
          </button>
        )}
      </div>

      {summary && (
        <div className="cards">
          <div className="card metric"><b>{summary.present}</b><span>Present</span></div>
          <div className="card metric"><b>{summary.late}</b><span>Late</span></div>
          <div className="card metric"><b>{summary.overtime}</b><span>Overtime</span></div>
          <div className="card metric"><b>{summary.missing_checkout}</b><span>Missing checkout</span></div>
          <div className="card metric"><b>{summary.absent}</b><span>Absent</span></div>
          <div className="card metric">
            <b>{summary.coverage_pct.toFixed(1)}%</b><span>Coverage (this month)</span>
          </div>
        </div>
      )}

      <h3>Recent records</h3>
      <AttendanceTable rows={rows} showNotes />
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
  // ?employee=<id> lets the employee directory deep-link straight into a
  // filtered attendance view for one person.
  const [employeeFilter, setEmployeeFilter] = useState(
    searchParams.get("employee") ?? "",
  );
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  // editor row: null = no correction open, else the row being corrected.
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
      setNotice(`Sweep complete — ${res.swept} open entries marked as missing checkout.`);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Sweep failed.");
    }
  }

  // --- manual entry (new row) -------------------------------------------
  const [form, setForm] = useState<TimesForm>(EMPTY_FORM);

  async function onManualCreate(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setNotice(null);
    const employee_id = Number(form.employee_id);
    if (!Number.isInteger(employee_id) || employee_id <= 0) {
      setError("Enter a valid employee id (numeric).");
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
      setNotice("Manual attendance entry created (marked as a correction).");
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to create entry.");
    }
  }

  // --- correction (existing row) ------------------------------------------
  const [correction, setCorrection] = useState<TimesForm>(EMPTY_FORM);
  const [saving, setSaving] = useState(false);

  function startCorrection(row: Attendance) {
    setError(null);
    setNotice(null);
    setCorrection({
      employee_id: String(row.employee_id),
      check_in: toLocalInput(row.check_in),
      check_out: toLocalInput(row.check_out) || "", // empty = keep closed as-is? open rows keep null
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
      // Always send what the user sees so the result is WYSIWYG; sending an
      // empty check_out would REOPEN a closed row, which is not allowed — the
      // backend treats unset as "unchanged", so only include it when filled.
      if (correction.check_in) changed.check_in = naiveIso(correction.check_in);
      if (correction.check_out) changed.check_out = naiveIso(correction.check_out);
      if (correction.notes !== (editing.notes ?? "")) {
        changed.notes = correction.notes || null;
      }
      await updateAttendance(editing.id, changed);
      setEditing(null);
      setNotice(`Entry #${editing.id} corrected and re-stamped (worked hours & status recomputed).`);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to save correction.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="stack">
      <div className="row spread">
        <h2>Attendance — all employees</h2>
        <button className="btn btn-ghost" onClick={() => void onSweep()}>
          Run missing-checkout sweep
        </button>
      </div>
      <ErrorBanner message={error} />
      {notice && <div className="alert alert-ok">{notice}</div>}

      <div className="row filters">
        <select value={status} onChange={(e) => setStatus(e.target.value as AttendanceStatus | "")}>
          <option value="">All statuses</option>
          {Object.entries(STATUS_LABEL).map(([v, label]) => (
            <option key={v} value={v}>{label}</option>
          ))}
        </select>
        <input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} />
        <input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} />
        <label className="check">
          <input
            type="checkbox"
            checked={manualOnly}
            onChange={(e) => setManualOnly(e.target.checked)}
          />
          Manual corrections only
        </label>
        <input
          type="number"
          placeholder="Employee id…"
          value={employeeFilter}
          onChange={(e) => setEmployeeFilter(e.target.value)}
        />
        {(status || manualOnly || employeeFilter || dateFrom || dateTo) && (
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
            Clear filters
          </button>
        )}
      </div>

      <AttendanceTable
        rows={rows}
        showNotes
        action={(row) => (
          <button className="btn btn-ghost btn-sm" onClick={() => startCorrection(row)}>
            Correct
          </button>
        )}
      />

      {editing === null ? (
        <div className="card">
          <h3>Manual entry (HR backfill / new record)</h3>
          <form className="row" onSubmit={onManualCreate}>
            <input
              type="number"
              required
              placeholder="Employee id"
              value={form.employee_id}
              onChange={(e) => setForm({ ...form, employee_id: e.target.value })}
            />
            <input
              type="datetime-local"
              required
              aria-label="Check in"
              value={form.check_in}
              onChange={(e) => setForm({ ...form, check_in: e.target.value })}
            />
            <input
              type="datetime-local"
              required
              aria-label="Check out"
              value={form.check_out}
              onChange={(e) => setForm({ ...form, check_out: e.target.value })}
            />
            <input
              placeholder="Notes (optional)"
              value={form.notes}
              onChange={(e) => setForm({ ...form, notes: e.target.value })}
            />
            <button className="btn btn-primary" type="submit">Add entry</button>
          </form>
          <div className="muted small">
            Employee picker arrives with Ameen's employee API; enter the numeric id for
            now (ids are shown in the list above).
          </div>
        </div>
      ) : (
        <div className="card">
          <div className="row spread">
            <h3>
              Correct entry #{editing.id} — {editing.employee_name ?? `employee ${editing.employee_id}`}
            </h3>
            <div className="row">
              <span className={`badge badge-${editing.status}`}>{STATUS_LABEL[editing.status]}</span>
              {editing.is_manual_correction && <span className="muted small">Already a correction</span>}
            </div>
          </div>
          <form className="row" onSubmit={saveCorrection}>
            <input
              type="datetime-local"
              required
              aria-label="Check in"
              value={correction.check_in}
              onChange={(e) => setCorrection({ ...correction, check_in: e.target.value })}
            />
            <input
              type="datetime-local"
              aria-label="Check out (empty = keep current)"
              value={correction.check_out}
              onChange={(e) => setCorrection({ ...correction, check_out: e.target.value })}
            />
            <input
              placeholder="Notes"
              value={correction.notes}
              onChange={(e) => setCorrection({ ...correction, notes: e.target.value })}
            />
            <button className="btn btn-primary" disabled={saving} type="submit">
              {saving ? "Saving…" : "Save correction"}
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
          </form>
          <div className="muted small">
            Saving re-derives worked hours &amp; status from the corrected times and stamps
            the change as a manual correction (history is never silently rewritten).
          </div>
        </div>
      )}
    </div>
  );
}

export function AttendancePage() {
  const { user, isHr } = useAuth();
  if (user === null) return <div className="muted">Loading…</div>;

  if (!isHr) {
    if (user.employee === null) {
      return (
        <div className="alert alert-error">
          This account is not linked to an employee — HR must link it before attendance
          self-service works.
        </div>
      );
    }
    return <EmployeeAttendance />;
  }
  return <HrAttendance />;
}
