import { useCallback, useEffect, useState, type FormEvent } from "react";

import {
  ApiError,
  approveAllocation,
  createAllocation,
  listAllocations,
  listTimeOffTypes,
  refuseAllocation,
} from "../api/client";
import type {
  AllocationStatus,
  TimeOffAllocation,
  TimeOffType,
} from "../api/types";
import { fmtDate, todayIso, useAuth } from "../auth";

const STATUS_LABEL: Record<AllocationStatus, string> = {
  draft: "Draft",
  to_approve: "Pending approval",
  approved: "Approved",
  refused: "Refused",
};

export function AllocationsPage() {
  const { isHr } = useAuth();
  const [rows, setRows] = useState<TimeOffAllocation[]>([]);
  const [types, setTypes] = useState<TimeOffType[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [form, setForm] = useState({
    employee_id: "",
    time_off_type_id: "",
    allocated_amount: "",
    valid_from: todayIso(),
    valid_to: "",
  });

  const load = useCallback(async () => {
    setError(null);
    try {
      const [allocs, typesPage] = await Promise.all([
        listAllocations(),
        listTimeOffTypes(),
      ]);
      setRows(allocs.items);
      setTypes(typesPage.items.filter((t) => t.is_active));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load allocations.");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  if (!isHr) return <div className="alert alert-error">Allocations are HR-only.</div>;

  async function onCreate(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setNotice(null);
    if (!form.employee_id.trim() || !form.time_off_type_id || !form.allocated_amount.trim()) {
      setError("Employee id, type and amount are required.");
      return;
    }
    setBusy(true);
    try {
      await createAllocation({
        employee_id: Number(form.employee_id),
        time_off_type_id: Number(form.time_off_type_id),
        allocated_amount: form.allocated_amount,
        valid_from: form.valid_from,
        valid_to: form.valid_to || null,
      });
      setForm((f) => ({
        ...f,
        employee_id: "",
        allocated_amount: "",
        valid_to: "",
      }));
      setNotice("Allocation created - pending approval. Approve it from the list below.");
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to create allocation.");
    } finally {
      setBusy(false);
    }
  }

  async function act(id: number, action: "approve" | "refuse") {
    setError(null);
    setNotice(null);
    setBusy(true);
    try {
      if (action === "approve") await approveAllocation(id);
      else await refuseAllocation(id);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Action failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="stack">
      <h2>Time off allocations (HR)</h2>
      {error && <div className="alert alert-error">{error}</div>}
      {notice && <div className="alert alert-ok">{notice}</div>}

      <div className="card">
        <h3>Grant balance to an employee</h3>
        <form className="grid" onSubmit={onCreate}>
          <label className="field">
            <span>Employee id</span>
            <input
              type="number"
              required
              value={form.employee_id}
              onChange={(e) => setForm({ ...form, employee_id: e.target.value })}
              placeholder="e.g. 3"
            />
          </label>
          <label className="field">
            <span>Type</span>
            <select
              value={form.time_off_type_id}
              onChange={(e) => setForm({ ...form, time_off_type_id: e.target.value })}
            >
              <option value="">Select…</option>
              {types.map((t) => (
                <option key={t.id} value={t.id}>{t.name}</option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Amount</span>
            <input
              type="number"
              step="0.5"
              min="0.5"
              required
              value={form.allocated_amount}
              onChange={(e) => setForm({ ...form, allocated_amount: e.target.value })}
            />
          </label>
          <label className="field">
            <span>Valid from</span>
            <input
              type="date"
              value={form.valid_from}
              onChange={(e) => setForm({ ...form, valid_from: e.target.value })}
            />
          </label>
          <label className="field">
            <span>Valid to (blank = open-ended)</span>
            <input
              type="date"
              min={form.valid_from}
              value={form.valid_to}
              onChange={(e) => setForm({ ...form, valid_to: e.target.value })}
              placeholder="Open-ended"
            />
          </label>
          <div className="field align-end">
            <button className="btn btn-primary" disabled={busy}>
              Create allocation
            </button>
          </div>
        </form>
        <div className="muted small">
          New allocations are created as <em>pending approval</em>; approve them below.
          Employee picker arrives with Ameen's employee API.
        </div>
      </div>

      <table className="table">
        <thead>
          <tr>
            <th>Employee</th>
            <th>Type</th>
            <th>Amount</th>
            <th>Valid</th>
            <th>Status</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((a) => (
            <tr key={a.id}>
              <td>{a.employee_name ?? `#${a.employee_id}`}</td>
              <td>{a.type_name ?? a.time_off_type_id}</td>
              <td>{a.allocated_amount}</td>
              <td>
                {fmtDate(a.valid_from)} → {a.valid_to ? fmtDate(a.valid_to) : "∞"}
              </td>
              <td>
                <span className={`badge badge-alloc-${a.status}`}>
                  {STATUS_LABEL[a.status]}
                </span>
              </td>
              <td className="row-actions">
                {a.status === "to_approve" && (
                  <>
                    <button
                      className="btn btn-ok btn-sm"
                      disabled={busy}
                      onClick={() => void act(a.id, "approve")}
                    >
                      Approve
                    </button>
                    <button
                      className="btn btn-danger btn-sm"
                      disabled={busy}
                      onClick={() => void act(a.id, "refuse")}
                    >
                      Refuse
                    </button>
                  </>
                )}
              </td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr><td colSpan={6} className="muted">No allocations.</td></tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
