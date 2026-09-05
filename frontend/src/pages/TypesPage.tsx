import { useCallback, useEffect, useState, type FormEvent } from "react";

import {
  ApiError,
  createTimeOffType,
  listTimeOffTypes,
  setTimeOffTypeActive,
} from "../api/client";
import type { TimeOffType, TimeOffUnit } from "../api/types";
import { useAuth } from "../auth";

export function TypesPage() {
  const { isHr } = useAuth();
  const [rows, setRows] = useState<TimeOffType[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const [form, setForm] = useState({
    name: "",
    unit: "days" as TimeOffUnit,
    requires_allocation: true,
    requires_approval: true,
    affects_payroll: false,
  });

  const load = useCallback(async () => {
    setError(null);
    try {
      const page = await listTimeOffTypes();
      setRows(page.items);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load types.");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function onCreate(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setNotice(null);
    if (!form.name.trim()) {
      setError("Name is required.");
      return;
    }
    try {
      await createTimeOffType({
        name: form.name.trim(),
        unit: form.unit,
        requires_allocation: form.requires_allocation,
        requires_approval: form.requires_approval,
        affects_payroll: form.affects_payroll,
      });
      setForm((f) => ({ ...f, name: "" }));
      setNotice(`Type created.`);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to create type.");
    }
  }

  async function toggleActive(t: TimeOffType) {
    setError(null);
    setNotice(null);
    try {
      await setTimeOffTypeActive(t.id, !t.is_active);
      setNotice(
        t.is_active
          ? `'${t.name}' deactivated.`
          : `'${t.name}' reactivated.`,
      );
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to update type.");
    }
  }

  return (
    <div className="stack">
      <h2>Time off types</h2>
      {error && <div className="alert alert-error">{error}</div>}
      {notice && <div className="alert alert-ok">{notice}</div>}

      {isHr && (
        <div className="card">
          <h3>New type (policy configuration)</h3>
          <form className="grid" onSubmit={onCreate}>
            <label className="field">
              <span>Name</span>
              <input
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                placeholder="e.g. Maternity Leave"
              />
            </label>
            <label className="field">
              <span>Unit</span>
              <select
                value={form.unit}
                onChange={(e) => setForm({ ...form, unit: e.target.value as TimeOffUnit })}
              >
                <option value="days">Days</option>
                <option value="hours">Hours</option>
              </select>
            </label>
            <label className="check">
              <input
                type="checkbox"
                checked={form.requires_allocation}
                onChange={(e) => setForm({ ...form, requires_allocation: e.target.checked })}
              />
              Requires allocation
            </label>
            <label className="check">
              <input
                type="checkbox"
                checked={form.requires_approval}
                onChange={(e) => setForm({ ...form, requires_approval: e.target.checked })}
              />
              Requires approval
            </label>
            <label className="check">
              <input
                type="checkbox"
                checked={form.affects_payroll}
                onChange={(e) => setForm({ ...form, affects_payroll: e.target.checked })}
              />
              Affects payroll
            </label>
            <button className="btn btn-primary" type="submit">Create type</button>
          </form>
        </div>
      )}

      <table className="table">
        <thead>
          <tr>
            <th>Name</th>
            <th>Unit</th>
            <th>Requires allocation</th>
            <th>Requires approval</th>
            <th>Affects payroll</th>
            <th>Status</th>
            {isHr && <th></th>}
          </tr>
        </thead>
        <tbody>
          {rows.map((t) => (
            <tr key={t.id}>
              <td>{t.name}</td>
              <td>{t.unit}</td>
              <td>{t.requires_allocation ? "Yes" : "No"}</td>
              <td>{t.requires_approval ? "Yes" : "No"}</td>
              <td>{t.affects_payroll ? "Yes" : "No"}</td>
              <td>
                <span className={`badge badge-${t.is_active ? "ok" : "muted"}`}>
                  {t.is_active ? "Active" : "Inactive"}
                </span>
              </td>
              {isHr && (
                <td>
                  <button
                    className="btn btn-ghost btn-sm"
                    onClick={() => void toggleActive(t)}
                    title="Deactivation is blocked while pending requests reference the type"
                  >
                    {t.is_active ? "Deactivate" : "Activate"}
                  </button>
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
