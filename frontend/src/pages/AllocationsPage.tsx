import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import "../styles.css"
import { typeBadgeClass } from "../api/leave";
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
import { fmtDate, fmtNum, todayIso, useAuth } from "../auth";

const STATUS_LABEL: Record<AllocationStatus, string> = {
  draft: "Draft",
  to_approve: "Pending approval",
  approved: "Approved",
  refused: "Refused",
};

function MetricCard({
  label,
  value,
  hint,
}: {
  label: string;
  value: string | number;
  hint?: string;
}) {
  return (
    <div className="card metric" style={{ padding: "16px", flex: 1, minWidth: 180 }}>
      <div className="row spread" style={{ marginBottom: 4 }}>
        <span className="muted small" style={{ fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.5px" }}>
          {label}
        </span>
      </div>
      <b style={{ fontSize: 24, display: "block", color: "#111827" }}>{value}</b>
      {hint && <small className="muted" style={{ marginTop: 4, display: "block" }}>{hint}</small>}
    </div>
  );
}

export function AllocationsPage() {
  const { isHr } = useAuth();
  const [rows, setRows] = useState<TimeOffAllocation[]>([]);
  const [types, setTypes] = useState<TimeOffType[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Filters
  const [statusFilter, setStatusFilter] = useState<string>("");
  const [typeFilter, setTypeFilter] = useState<string>("");
  const [searchEmployee, setSearchEmployee] = useState<string>("");

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

  if (!isHr) return <div className="alert alert-error">Allocations management is HR-only.</div>;

  async function onCreate(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setNotice(null);
    if (!form.employee_id.trim() || !form.time_off_type_id || !form.allocated_amount.trim()) {
      setError("Employee ID, type, and amount are required.");
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
      setNotice("Allocation created successfully. It is now pending approval.");
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

  // Filtered Rows
  const filteredRows = useMemo(() => {
    return rows.filter((r) => {
      const empName = (r.employee_name ?? `#$typeBadgeClass{r.employee_id}`).toLowerCase();
      const matchesSearch =
        !searchEmployee.trim() ||
        empName.includes(searchEmployee.toLowerCase().trim()) ||
        String(r.employee_id).includes(searchEmployee.trim());

      const matchesStatus = !statusFilter || r.status === statusFilter;
      const matchesType = !typeFilter || String(r.time_off_type_id) === typeFilter;

      return matchesSearch && matchesStatus && matchesType;
    });
  }, [rows, searchEmployee, statusFilter, typeFilter]);

  // Statistics
  const stats = useMemo(() => {
    let pending = 0;
    let approved = 0;
    let totalGranted = 0;

    rows.forEach((r) => {
      if (r.status === "to_approve") pending++;
      if (r.status === "approved") {
        approved++;
        totalGranted += Number(r.allocated_amount) || 0;
      }
    });

    return {
      total: rows.length,
      pending,
      approved,
      totalGranted,
    };
  }, [rows]);

  return (
    <div className="stack" style={{ gap: 20 }}>
      {/* Header */}
      <div className="row spread" style={{ alignItems: "center" }}>
        <div>
          <h2 style={{ margin: 0, fontSize: 22, fontWeight: 700 }}>Time Off Allocations</h2>
          <p className="muted small" style={{ margin: "4px 0 0" }}>
            Grant leave balances to employees and manage pending approval requests.
          </p>
        </div>
      </div>

      {error && <div className="alert alert-error">{error}</div>}
      {notice && <div className="alert alert-ok">{notice}</div>}

      {/* KPI Metrics */}
      <div className="cards" style={{ gap: 12 }}>
        <MetricCard label="Total Grants" value={fmtNum(stats.total)} hint="All allocation records" />
        <MetricCard label="Pending Approval" value={fmtNum(stats.pending)} hint="Awaiting HR sign-off" />
        <MetricCard label="Approved Grants" value={fmtNum(stats.approved)} hint="Active balance grants" />
        <MetricCard label="Total Days Granted" value={`${fmtNum(stats.totalGranted)} days`} hint="Sum of approved days" />
      </div>

      {/* Grant Allocation Form Card */}
      <div className="card" style={{ padding: 20 }}>
        <h3 style={{ margin: "0 0 14px", fontSize: 16, fontWeight: 600 }}>Grant Leave Balance</h3>
        <form onSubmit={onCreate} className="stack" style={{ gap: 16 }}>
          <div className="form-grid" style={{ marginBottom: 0 }}>
            <label className="field">
              <span>Employee ID</span>
              <input
                type="number"
                required
                value={form.employee_id}
                onChange={(e) => setForm({ ...form, employee_id: e.target.value })}
                placeholder="e.g. 3"
              />
            </label>

            <label className="field">
              <span>Leave Type</span>
              <select
                required
                value={form.time_off_type_id}
                onChange={(e) => setForm({ ...form, time_off_type_id: e.target.value })}
              >
                <option value="">Select leave type…</option>
                {types.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.name}
                  </option>
                ))}
              </select>
            </label>

            <label className="field">
              <span>Amount (Days / Hours)</span>
              <input
                type="number"
                step="0.5"
                min="0.5"
                required
                value={form.allocated_amount}
                onChange={(e) => setForm({ ...form, allocated_amount: e.target.value })}
                placeholder="e.g. 15"
              />
            </label>

            <label className="field">
              <span>Valid From</span>
              <input
                type="date"
                value={form.valid_from}
                onChange={(e) => setForm({ ...form, valid_from: e.target.value })}
              />
            </label>

            <label className="field">
              <span>Valid To</span>
              <input
                type="date"
                min={form.valid_from}
                value={form.valid_to}
                onChange={(e) => setForm({ ...form, valid_to: e.target.value })}
                placeholder="Leave blank for open-ended"
              />
            </label>

            <div className="row-actions" style={{ alignItems: "flex-end" }}>
              <button className="btn btn-primary" disabled={busy}>
                Create Allocation
              </button>
            </div>
          </div>
        </form>
      </div>

      {/* Filter Bar */}
      <div className="card" style={{ padding: 14 }}>
        <div className="form-grid" style={{ marginBottom: 0 }}>
          <label className="field">
            <span>Search Employee</span>
            <input
              type="text"
              placeholder="Search name or ID…"
              value={searchEmployee}
              onChange={(e) => setSearchEmployee(e.target.value)}
            />
          </label>

          <label className="field">
            <span>Filter Status</span>
            <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
              <option value="">All Statuses</option>
              <option value="to_approve">Pending approval</option>
              <option value="approved">Approved</option>
              <option value="refused">Refused</option>
              <option value="draft">Draft</option>
            </select>
          </label>

          <label className="field">
            <span>Filter Leave Type</span>
            <select value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)}>
              <option value="">All Leave Types</option>
              {types.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name}
                </option>
              ))}
            </select>
          </label>

          {(searchEmployee || statusFilter || typeFilter) && (
            <div className="row-actions" style={{ alignItems: "flex-end" }}>
              <button
                className="btn btn-ghost btn-sm"
                onClick={() => {
                  setSearchEmployee("");
                  setStatusFilter("");
                  setTypeFilter("");
                }}
              >
                Reset Filters
              </button>
            </div>
          )}
        </div>
      </div>

      {/* Allocations Table */}
      <div className="card" style={{ padding: 0, overflow: "hidden" }}>
        <table className="table" style={{ margin: 0 }}>
          <thead>
            <tr>
              <th>Employee</th>
              <th>Leave Type</th>
              <th>Allocated Amount</th>
              <th>Validity Period</th>
              <th>Status</th>
              <th style={{ textAlign: "right" }}>Actions</th>
            </tr>
          </thead>
          <tbody>
            {filteredRows.map((a) => (
              <tr key={a.id}>
                <td>
                  <b style={{ color: "#111827" }}>{a.employee_name ?? `Employee #${a.employee_id}`}</b>
                  <div className="small muted">ID #{a.employee_id}</div>
                </td>
                <td>
                  <span className="badge badge-muted">{a.type_name ?? `Type #${a.time_off_type_id}`}</span>
                </td>
                <td>
                  <b>{a.allocated_amount}</b>
                </td>
                <td>
                  {fmtDate(a.valid_from)} → {a.valid_to ? fmtDate(a.valid_to) : "Open-ended"}
                </td>
                <td>
                  <span
                    className={`badge ${
                      a.status === "approved"
                        ? "badge-ok"
                        : a.status === "to_approve"
                        ? "badge-req-to_approve"
                        : a.status === "refused"
                        ? "badge-absent"
                        : "badge-muted"
                    }`}
                  >
                    {STATUS_LABEL[a.status]}
                  </span>
                </td>
                <td style={{ textAlign: "right" }}>
                  {a.status === "to_approve" ? (
                    <div className="row-actions" style={{ justifyContent: "flex-end" }}>
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
                    </div>
                  ) : (
                    <span className="small muted">—</span>
                  )}
                </td>
              </tr>
            ))}
            {filteredRows.length === 0 && (
              <tr>
                <td colSpan={6} className="muted" style={{ textAlign: "center", padding: 24 }}>
                  No allocation records found.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
