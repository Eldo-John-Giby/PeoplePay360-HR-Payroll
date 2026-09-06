import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { useSearchParams } from "react-router-dom";
import "../styles.css"
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
import { addDaysIso, fmtDate, fmtNum, todayIso, useAuth } from "../auth";
import { typeBadgeClass } from "../api/leave";

const STATUS_LABEL: Record<RequestStatus, string> = {
  draft: "Draft",
  to_approve: "Pending approval",
  approved: "Approved",
  refused: "Refused",
  cancelled: "Cancelled",
};

function MetricCard({
  label,
  value,
  hint,
  tone,
}: {
  label: string;
  value: string | number;
  hint?: string;
  tone?: "ok" | "warn" | "danger";
}) {
  return (
    <div className="card metric" style={{ padding: "16px", flex: 1, minWidth: 180 }}>
      <div className="row spread" style={{ marginBottom: 4 }}>
        <span className="muted small" style={{ fontWeight: 600 }}>
          {label}
        </span>
      </div>
      <b
        className={tone}
        style={{ fontSize: 24, display: "block", color: tone ? undefined : "#111827" }}
      >
        {value}
      </b>
      {hint && <small className="muted" style={{ marginTop: 4, display: "block" }}>{hint}</small>}
    </div>
  );
}

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

  // Filters
  const [statusFilter, setStatusFilter] = useState<string>("");
  const [searchQuery, setSearchQuery] = useState<string>("");

  const [form, setForm] = useState({
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
      setError("Please select a time off type.");
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
      } else {
        setNotice("Time off request submitted successfully.");
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

  function onDates(date_from: string, date_to: string) {
    setForm((f) => ({ ...f, date_from, date_to }));
    const start = new Date(`${date_from}T00:00:00`);
    const end = new Date(`${date_to}T00:00:00`);
    if (!Number.isNaN(start.getTime()) && !Number.isNaN(end.getTime()) && end >= start) {
      const days = Math.round((end.getTime() - start.getTime()) / 86400000) + 1;
      setForm((f) => ({ ...f, duration: String(days) }));
    }
  }

  // Filtered rows
  const filteredRows = useMemo(() => {
    return rows.filter((r) => {
      const empName = (r.employee_name ?? `#${r.employee_id}`).toLowerCase();
      const reasonText = (r.reason ?? "").toLowerCase();
      const matchesSearch =
        !searchQuery.trim() ||
        empName.includes(searchQuery.toLowerCase().trim()) ||
        reasonText.includes(searchQuery.toLowerCase().trim());

      const matchesStatus = !statusFilter || r.status === statusFilter;

      return matchesSearch && matchesStatus;
    });
  }, [rows, searchQuery, statusFilter]);

  // Metric stats
  const stats = useMemo(() => {
    let pending = 0;
    let approved = 0;
    let refused = 0;

    rows.forEach((r) => {
      if (r.status === "to_approve") pending++;
      if (r.status === "approved") approved++;
      if (r.status === "refused") refused++;
    });

    return {
      total: rows.length,
      pending,
      approved,
      refused,
    };
  }, [rows]);

  return (
    <div className="stack" style={{ gap: 20 }}>
      {/* Header */}
      <div className="row spread" style={{ alignItems: "center" }}>
        <div>
          <h2 style={{ margin: 0, fontSize: 22, fontWeight: 700 }}>Time Off Requests</h2>
          <p className="muted small" style={{ margin: "4px 0 0" }}>
            Submit new leave requests and review pending approvals across your team.
          </p>
        </div>
      </div>

      <ErrorBanner message={error} />
      {notice && <div className="alert alert-ok">{notice}</div>}

      {/* Metric Cards — tone reflects real status severity, not decoration */}
      <div className="cards" style={{ gap: 12 }}>
        <MetricCard label="Total Requests" value={fmtNum(stats.total)} hint="All submitted requests" />
        <MetricCard
          label="Pending Approval"
          value={fmtNum(stats.pending)}
          hint="Awaiting review"
          tone={stats.pending > 0 ? "warn" : undefined}
        />
        <MetricCard label="Approved" value={fmtNum(stats.approved)} hint="Granted leave" tone="ok" />
        <MetricCard
          label="Refused"
          value={fmtNum(stats.refused)}
          hint="Declined requests"
          tone={stats.refused > 0 ? "danger" : undefined}
        />
      </div>

      {/* New Request Form Card */}
      <div className="card" style={{ padding: 20 }}>
        <h3 style={{ margin: "0 0 14px", fontSize: 16, fontWeight: 600 }}>Submit Time Off Request</h3>
        <form onSubmit={onCreate} className="stack" style={{ gap: 14 }}>
          <div className="form-grid" style={{ marginBottom: 0 }}>
            {isHr && (
              <label className="field">
                <span>Employee ID (blank for self)</span>
                <input
                  type="number"
                  value={form.employee_id}
                  onChange={(e) => setForm({ ...form, employee_id: e.target.value })}
                  placeholder="e.g. 5"
                />
              </label>
            )}

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
                    {t.name} ({t.unit})
                  </option>
                ))}
              </select>
            </label>

            <label className="field">
              <span>Date From</span>
              <input
                type="date"
                required
                value={form.date_from}
                onChange={(e) => onDates(e.target.value, form.date_to)}
              />
            </label>

            <label className="field">
              <span>Date To</span>
              <input
                type="date"
                required
                value={form.date_to}
                onChange={(e) => onDates(form.date_from, e.target.value)}
              />
            </label>

            <label className="field">
              <span>Duration Amount</span>
              <input
                type="number"
                step="0.5"
                min="0.5"
                required
                value={form.duration}
                onChange={(e) => setForm({ ...form, duration: e.target.value })}
              />
            </label>

            <label className="field field-wide">
              <span>Reason / Notes (Optional)</span>
              <input
                value={form.reason}
                onChange={(e) => setForm({ ...form, reason: e.target.value })}
                placeholder="Specify reason for request…"
              />
            </label>

            <div className="row-actions" style={{ alignItems: "flex-end" }}>
              <button className="btn btn-primary" disabled={busy}>
                Submit Request
              </button>
            </div>
          </div>
        </form>
      </div>

      {/* Filter Bar */}
      <div className="card" style={{ padding: 14 }}>
        <div className="form-grid" style={{ marginBottom: 0 }}>
          <label className="field">
            <span>Search</span>
            <input
              type="text"
              placeholder="Search employee or reason…"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
            />
          </label>

          <label className="field">
            <span>Filter Status</span>
            <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
              <option value="">All Statuses</option>
              <option value="to_approve">Pending approval</option>
              <option value="approved">Approved</option>
              <option value="refused">Refused</option>
              <option value="cancelled">Cancelled</option>
            </select>
          </label>

          {(searchQuery || statusFilter) && (
            <div className="row-actions" style={{ alignItems: "flex-end" }}>
              <button
                className="btn btn-ghost btn-sm"
                onClick={() => {
                  setSearchQuery("");
                  setStatusFilter("");
                }}
              >
                Reset Filters
              </button>
            </div>
          )}
        </div>
      </div>

      {/* Table */}
      <div className="card" style={{ padding: 0, overflow: "hidden" }}>
        <table className="table" style={{ margin: 0 }}>
          <thead>
            <tr>
              <th>Employee</th>
              <th>Leave Type</th>
              <th>Dates</th>
              <th>Duration</th>
              <th>Status</th>
              <th>Reason</th>
              <th style={{ textAlign: "right" }}>Actions</th>
            </tr>
          </thead>
          <tbody>
            {filteredRows.map((r) => (
              <tr key={r.id}>
                <td>
                  <b style={{ color: "#111827" }}>{r.employee_name ?? `Employee #${r.employee_id}`}</b>
                  <div className="small muted">ID #{r.employee_id}</div>
                </td>
                <td>
                  <span className={`badge ${typeBadgeClass(r.type_name ?? `Type #${r.time_off_type_id}`)}`}>
                    {r.type_name ?? `Type #${r.time_off_type_id}`}
                  </span>
                </td>
                <td>
                  {fmtDate(r.date_from)} → {fmtDate(r.date_to)}
                </td>
                <td>
                  <b>{r.duration}</b> <span className="small muted">{r.unit ?? ""}</span>
                </td>
                <td>
                  <span
                    className={`badge ${
                      r.status === "approved"
                        ? "badge-ok"
                        : r.status === "to_approve"
                        ? "badge-req-to_approve"
                        : r.status === "refused"
                        ? "badge-absent"
                        : "badge-muted"
                    }`}
                  >
                    {STATUS_LABEL[r.status]}
                  </span>
                </td>
                <td className="small">
                  {r.reason ? <span className="muted">{r.reason}</span> : <span className="muted-faint">—</span>}
                </td>
                <td style={{ textAlign: "right" }}>
                  <div className="row-actions" style={{ justifyContent: "flex-end" }}>
                    {isHr && r.status === "to_approve" && (
                      <>
                        <button className="btn btn-ok btn-sm" disabled={busy} onClick={() => void act(r.id, "approve")}>
                          Approve
                        </button>
                        <button className="btn btn-danger btn-sm" disabled={busy} onClick={() => void act(r.id, "refuse")}>
                          Refuse
                        </button>
                      </>
                    )}
                    {isHr && r.status === "approved" && (
                      <button className="btn btn-ghost btn-sm" disabled={busy} onClick={() => void act(r.id, "cancel")}>
                        Cancel
                      </button>
                    )}
                    {!isHr && r.status === "to_approve" && user?.employee_id === r.employee_id && (
                      <button className="btn btn-ghost btn-sm" disabled={busy} onClick={() => void act(r.id, "cancel")}>
                        Cancel
                      </button>
                    )}
                    {(r.status === "approved" || r.status === "refused") &&
                      !isHr &&
                      user?.employee_id === r.employee_id && (
                        <span className="muted small">Decision by HR</span>
                      )}
                  </div>
                </td>
              </tr>
            ))}
            {filteredRows.length === 0 && (
              <tr>
                <td colSpan={7} className="muted" style={{ textAlign: "center", padding: 24 }}>
                  No time off requests found.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}