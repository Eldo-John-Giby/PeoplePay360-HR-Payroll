import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import {
  ApiError,
  getAttendanceOverview,
  getDashboardKpis,
  getMonthlyTrend,
  getPayrollAlerts,
  getSalaryByDepartment,
  getTimeOffOverview,
  listDepartments,
} from "../api/client";
import type {
  AttendanceOverview,
  DashboardKpis,
  DepartmentSummary,
  MonthlyTrendItem,
  PayrollAlerts,
  SalaryByDepartmentItem,
  TimeOffOverview,
} from "../api/types";
import { fmtDate, useAuth } from "../auth";
import { fmtMoney } from "./ContractsPage";

const ALERT_LABEL: Record<string, string> = {
  missing_bank_details: "Missing bank details",
  duplicate_payslip: "Duplicate payslip",
  missing_contract: "No active contract",
  negative_net: "Negative net salary",
  overlapping_period: "Overlapping period",
  other: "Other",
};

function fmtMonth(m: string): string {
  const [y, mo] = m.split("-");
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  return `${months[Number(mo) - 1]} ${y}`;
}

export function PayrollDashboardPage() {
  const { hasRole } = useAuth();
  const isPayroll = hasRole("HR_PAYROLL_USER", "HR_PAYROLL_MANAGER", "ADMIN");

  const [kpis, setKpis] = useState<DashboardKpis | null>(null);
  const [byDept, setByDept] = useState<SalaryByDepartmentItem[]>([]);
  const [trend, setTrend] = useState<MonthlyTrendItem[]>([]);
  const [attendance, setAttendance] = useState<AttendanceOverview | null>(null);
  const [timeoff, setTimeoff] = useState<TimeOffOverview | null>(null);
  const [alerts, setAlerts] = useState<PayrollAlerts | null>(null);
  const [departments, setDepartments] = useState<DepartmentSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Filters
  const [periodStart, setPeriodStart] = useState("");
  const [periodEnd, setPeriodEnd] = useState("");
  const [deptFilter, setDeptFilter] = useState("");
  const [empType, setEmpType] = useState("");

  const filters = useMemo(
    () => ({
      period_start: periodStart || undefined,
      period_end: periodEnd || undefined,
      department_id: deptFilter ? Number(deptFilter) : undefined,
      employee_type: empType || undefined,
    }),
    [periodStart, periodEnd, deptFilter, empType],
  );

  const load = useCallback(async () => {
    setError(null);
    setBusy(true);
    try {
      const [k, d, t, a, to, al, depts] = await Promise.all([
        getDashboardKpis(filters),
        getSalaryByDepartment(filters),
        getMonthlyTrend(6, filters),
        getAttendanceOverview(filters),
        getTimeOffOverview(filters),
        getPayrollAlerts(filters),
        listDepartments(),
      ]);
      setKpis(k);
      setByDept(d);
      setTrend(t);
      setAttendance(a);
      setTimeoff(to);
      setAlerts(al);
      setDepartments(depts.items);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load dashboard.");
    } finally {
      setBusy(false);
    }
  }, [filters]);

  useEffect(() => {
    void load();
  }, [load]);

  if (!isPayroll) {
    return <div className="alert alert-error">Payroll dashboard is restricted.</div>;
  }

  const maxDept = byDept.length
    ? Math.max(...byDept.map((d) => Number(d.total_salary)))
    : 1;

  return (
    <div className="stack">
      <div className="row spread">
        <h2>Payroll dashboard</h2>
        <button className="btn btn-ghost btn-sm" onClick={() => void load()} disabled={busy}>
          {busy ? "Refreshing…" : "Refresh"}
        </button>
      </div>
      {error && <div className="alert alert-error">{error}</div>}

      {/* Filter bar ---------------------------------------------------------- */}
      <div className="card">
        <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))" }}>
          <label className="field">
            <span>Period from</span>
            <input type="date" value={periodStart} onChange={(e) => setPeriodStart(e.target.value)} />
          </label>
          <label className="field">
            <span>Period to</span>
            <input type="date" value={periodEnd} onChange={(e) => setPeriodEnd(e.target.value)} />
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
          <label className="field">
            <span>Employee type</span>
            <select value={empType} onChange={(e) => setEmpType(e.target.value)}>
              <option value="">All</option>
              <option value="full_time">Full-time</option>
              <option value="part_time">Part-time</option>
              <option value="contract">Contract</option>
              <option value="intern">Intern</option>
            </select>
          </label>
        </div>
        {(periodStart || periodEnd) && (
          <p className="muted small" style={{ marginTop: 8 }}>
            Showing {periodStart ? fmtDate(periodStart) : "…"} → {periodEnd ? fmtDate(periodEnd) : "…"}.
            All figures are computed live from current data + filters.
          </p>
        )}
      </div>

      {/* KPI cards ------------------------------------------------------------ */}
      <div className="cards" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))" }}>
        <div className="card metric">
          <b>{fmtMoney(kpis?.total_net_salary_paid)}</b>
          <span>Total net salary paid</span>
        </div>
        <div className="card metric">
          <b>{kpis?.payslips_generated ?? 0}</b>
          <span>Payslips generated</span>
        </div>
        <div className="card metric">
          <b>{fmtMoney(kpis?.average_salary)}</b>
          <span>Average salary</span>
        </div>
        <div className="card metric">
          <b>{fmtMoney(kpis?.approved_time_off_days)}</b>
          <span>Approved time off (days)</span>
        </div>
        <div className="card metric">
          <b>{kpis ? `${kpis.attendance_health_pct.toFixed(1)}%` : "—"}</b>
          <span>Attendance health</span>
        </div>
      </div>

      {/* Charts --------------------------------------------------------------- */}
      <div className="grid" style={{ gridTemplateColumns: "1fr 1fr" }}>
        <div className="card">
          <h3 style={{ fontSize: 14 }}>Salary cost by department</h3>
          {byDept.length === 0 ? (
            <p className="muted small">No paid payroll in this period.</p>
          ) : (
            <ResponsiveContainer width="100%" height={240}>
              <BarChart data={byDept} margin={{ top: 8, right: 8, left: 0, bottom: 8 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
                <XAxis dataKey="department_name" tick={{ fontSize: 11 }} interval={0} angle={-12} textAnchor="end" height={46} />
                <YAxis tick={{ fontSize: 11 }} />
                <Tooltip formatter={(v) => fmtMoney(String(v ?? ""))} />
                <Bar dataKey="total_salary" name="Total salary" fill="#714b67" radius={[6, 6, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          )}
        </div>

        <div className="card">
          <h3 style={{ fontSize: 14 }}>Monthly net salary trend</h3>
          {trend.length === 0 ? (
            <p className="muted small">No trend data.</p>
          ) : (
            <ResponsiveContainer width="100%" height={240}>
              <LineChart data={trend} margin={{ top: 8, right: 8, left: 0, bottom: 8 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
                <XAxis dataKey="month" tick={{ fontSize: 11 }} tickFormatter={fmtMonth} />
                <YAxis tick={{ fontSize: 11 }} />
                <Tooltip
                  labelFormatter={(l) => fmtMonth(String(l ?? ""))}
                  formatter={(v) => fmtMoney(String(v ?? ""))}
                />
                <Line
                  type="monotone"
                  dataKey="total_net_salary"
                  name="Net salary"
                  stroke="#714b67"
                  strokeWidth={2.5}
                  dot={{ r: 4, fill: "#714b67" }}
                />
              </LineChart>
            </ResponsiveContainer>
          )}
        </div>
      </div>

      {/* Alerts + overviews --------------------------------------------------- */}
      <div className="grid" style={{ gridTemplateColumns: "1fr 1fr" }}>
        <div className="card">
          <h3 style={{ fontSize: 14 }}>Operational alerts</h3>
          {alerts && alerts.alerts.length === 0 ? (
            <p className="muted small">All clear — no open payslip warnings.</p>
          ) : (
            <ul className="warn-list">
              {(alerts?.alerts ?? []).map((a) => (
                <li key={a.warning_type}>
                  <b>{ALERT_LABEL[a.warning_type] ?? a.warning_type}</b> — {a.count} payslip(s)
                  <span className="small muted"> (ids: {a.payslip_ids.slice(0, 5).join(", ")}{a.payslip_ids.length > 5 ? "…" : ""})</span>
                </li>
              ))}
            </ul>
          )}
          {alerts && alerts.total_open_payslips > 0 && (
            <p className="muted small">
              {alerts.total_open_payslips} open payslip(s) with warnings across draft/computed runs.
            </p>
          )}
        </div>

        <div className="card">
          <h3 style={{ fontSize: 14 }}>Attendance overview</h3>
          {attendance && (
            <div className="grid" style={{ gridTemplateColumns: "repeat(3, 1fr)" }}>
              <div className="metric"><b className="ok">{attendance.present}</b><span>Present</span></div>
              <div className="metric"><b className="warn">{attendance.late}</b><span>Late</span></div>
              <div className="metric"><b className="danger">{attendance.absent}</b><span>Absent</span></div>
              <div className="metric"><b>{attendance.overtime}</b><span>Overtime</span></div>
              <div className="metric"><b className="danger">{attendance.missing_checkouts}</b><span>Missing check-out</span></div>
              <div className="metric"><b>{attendance.manual_edits}</b><span>Manual edits</span></div>
            </div>
          )}
          {attendance && (
            <p className="muted small" style={{ marginTop: 10 }}>
              Coverage: <b>{attendance.coverage_pct.toFixed(1)}%</b> — computed from the
              attendance module&apos;s own summary logic (no duplication here).
            </p>
          )}
        </div>
      </div>

      <div className="grid" style={{ gridTemplateColumns: "1fr 1fr" }}>
        <div className="card">
          <h3 style={{ fontSize: 14 }}>Time off overview</h3>
          {timeoff && (
            <>
              <div className="row" style={{ marginBottom: 10 }}>
                <span className="badge badge-ok">Approved: {fmtMoney(timeoff.approved_days)} days</span>
                <span className="badge badge-alloc-to_approve">Pending: {timeoff.pending_requests}</span>
              </div>
              {(timeoff.balances_by_type ?? []).map((b) => (
                <div key={b.time_off_type_name} className="ld-balance" style={{ padding: "8px 10px" }}>
                  <div className="ld-bal-top">
                    <span>{b.time_off_type_name}</span>
                    <b>{fmtMoney(b.remaining)}</b>
                  </div>
                </div>
              ))}
            </>
          )}
        </div>

        <div className="card">
          <h3 style={{ fontSize: 14 }}>Department breakdown</h3>
          <table className="table">
            <thead>
              <tr>
                <th>Department</th>
                <th>Headcount</th>
                <th style={{ textAlign: "right" }}>Total salary</th>
              </tr>
            </thead>
            <tbody>
              {byDept.map((d) => (
                <tr key={d.department_name}>
                  <td><b>{d.department_name}</b></td>
                  <td>{d.headcount}</td>
                  <td style={{ textAlign: "right" }}>{fmtMoney(d.total_salary)}</td>
                </tr>
              ))}
              {byDept.length === 0 && (
                <tr><td colSpan={3} className="muted">No data for the current filters.</td></tr>
              )}
            </tbody>
          </table>
          {maxDept > 1 && (
            <div className="ld-bar" style={{ marginTop: 12 }}>
              <i style={{ width: "100%", background: "linear-gradient(90deg,#714b67,#a37f97)" }} />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}