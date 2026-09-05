import { useCallback, useEffect, useState, type FormEvent } from "react";
import { useSearchParams } from "react-router-dom";

import {
  ApiError,
  approveRequest,
  cancelRequest,
  createRequest,
  listRequests,
  listTimeOffTypes,
  refuseRequest,
} from "../api/client";
import type { RequestStatus, TimeOffRequest, TimeOffType } from "../api/types";
import { addDaysIso, fmtDate, todayIso, useAuth } from "../auth";

const STATUS_LABEL: Record<RequestStatus, string> = {
  draft: "Draft",
  to_approve: "Pending approval",
  approved: "Approved",
  refused: "Refused",
  cancelled: "Cancelled",
};

function ErrorBanner({ message }: { message: string | null }) {
  if (!message) return null;
  return <div className="alert alert-error">{message}</div>;
}

export function TimeOffRequestsPage() {
  const { isHr, user } = useAuth();
  const [searchParams] = useSearchParams();
  const [rows, setRows] = useState<TimeOffRequest[]>([]);
  const [types, setTypes] = useState<TimeOffType[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [form, setForm] = useState({
    // ?employee=<id> (HR) prefills "on behalf of" from the directory link.
    employee_id: searchParams.get("employee") ?? "",
    time_off_type_id: "",
    date_from: todayIso(),
    date_to: addDaysIso(1),
    duration: "1",
    reason: "",
  });

  const load = useCallback(async () => {
    setError(null);
    try {
      const [reqs, typesPage] = await Promise.all([
        listRequests({}, 100),
        listTimeOffTypes(),
      ]);
      setRows(reqs.items);
      setTypes(typesPage.items.filter((t) => t.is_active));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load requests.");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function onCreate(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setNotice(null);
    if (!form.time_off_type_id) {
      setError("Pick a time off type.");
      return;
    }
    setBusy(true);
    try {
      const created = await createRequest({
        employee_id: isHr && form.employee_id.trim() ? Number(form.employee_id) : undefined,
        time_off_type_id: Number(form.time_off_type_id),
        date_from: form.date_from,
        date_to: form.date_to,
        duration: form.duration,
        reason: form.reason || null,
      });
      if (created.warnings.length > 0) {
        setNotice(created.warnings.join(" "));
      }
      setForm((f) => ({ ...f, reason: "" }));
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to submit request.");
    } finally {
      setBusy(false);
    }
  }

  async function act(id: number, action: "approve" | "refuse" | "cancel") {
    setError(null);
    setNotice(null);
    setBusy(true);
    try {
      if (action === "approve") await approveRequest(id);
      else if (action === "refuse") await refuseRequest(id);
      else await cancelRequest(id);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Action failed.");
    } finally {
      setBusy(false);
    }
  }

  // When the date range changes, propose a day-unit duration automatically.
  function onDates(date_from: string, date_to: string) {
    setForm((f) => ({ ...f, date_from, date_to }));
    const start = new Date(`${date_from}T00:00:00`);
    const end = new Date(`${date_to}T00:00:00`);
    if (!Number.isNaN(start.getTime()) && !Number.isNaN(end.getTime()) && end >= start) {
      const days = Math.round((end.getTime() - start.getTime()) / 86400000) + 1;
      setForm((f) => ({ ...f, duration: String(days) }));
    }
  }

  return (
    <div className="stack">
      <h2>Time off requests</h2>
      <ErrorBanner message={error} />
      {notice && <div className="alert alert-ok">{notice}</div>}

      <div className="card">
        <h3>Request leave</h3>
        <form className="grid" onSubmit={onCreate}>
          {isHr && (
            <label className="field">
              <span>Employee id (blank = yourself)</span>
              <input
                type="number"
                value={form.employee_id}
                onChange={(e) => setForm({ ...form, employee_id: e.target.value })}
                placeholder="e.g. 5"
              />
            </label>
          )}
          <label className="field">
            <span>Type</span>
            <select
              value={form.time_off_type_id}
              onChange={(e) => setForm({ ...form, time_off_type_id: e.target.value })}
            >
              <option value="">Select…</option>
              {types.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name} ({t.unit})
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>From</span>
            <input
              type="date"
              value={form.date_from}
              onChange={(e) => onDates(e.target.value, form.date_to)}
            />
          </label>
          <label className="field">
            <span>To</span>
            <input
              type="date"
              value={form.date_to}
              onChange={(e) => onDates(form.date_from, e.target.value)}
            />
          </label>
          <label className="field">
            <span>Duration (in type's unit)</span>
            <input
              type="number"
              step="0.5"
              min="0.5"
              value={form.duration}
              onChange={(e) => setForm({ ...form, duration: e.target.value })}
            />
          </label>
          <label className="field field-wide">
            <span>Reason</span>
            <input
              value={form.reason}
              onChange={(e) => setForm({ ...form, reason: e.target.value })}
              placeholder="Optional"
            />
          </label>
          <button className="btn btn-primary" disabled={busy}>
            Submit request
          </button>
        </form>
      </div>

      <table className="table">
        <thead>
          <tr>
            <th>Employee</th>
            <th>Type</th>
            <th>Dates</th>
            <th>Duration</th>
            <th>Status</th>
            <th>Reason</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id}>
              <td>{r.employee_name ?? `#${r.employee_id}`}</td>
              <td>{r.type_name ?? r.time_off_type_id}</td>
              <td>
                {fmtDate(r.date_from)} → {fmtDate(r.date_to)}
              </td>
              <td>{r.duration} {r.unit ?? ""}</td>
              <td><span className={`badge badge-req-${r.status}`}>{STATUS_LABEL[r.status]}</span></td>
              <td>{r.reason ?? ""}</td>
              <td className="row-actions">
                {isHr && r.status === "to_approve" && (
                  <>
                    <button className="btn btn-ok btn-sm" onClick={() => void act(r.id, "approve")}>
                      Approve
                    </button>
                    <button className="btn btn-danger btn-sm" onClick={() => void act(r.id, "refuse")}>
                      Refuse
                    </button>
                  </>
                )}
                {isHr && r.status === "approved" && (
                  <button className="btn btn-ghost btn-sm" onClick={() => void act(r.id, "cancel")}>
                    Cancel
                  </button>
                )}
                {!isHr && r.status === "to_approve" && user?.employee_id === r.employee_id && (
                  <button className="btn btn-ghost btn-sm" onClick={() => void act(r.id, "cancel")}>
                    Cancel
                  </button>
                )}
                {(r.status === "approved" || r.status === "refused") &&
                  !isHr && user?.employee_id === r.employee_id && (
                    <span className="muted small">Decision by HR</span>
                  )}
              </td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr><td colSpan={7} className="muted">No time off requests.</td></tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
