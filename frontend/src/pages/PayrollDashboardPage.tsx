// Payroll dashboard — live analytics over /api/v1/dashboard/* with a
// composable filter bar (period + department + employee type + company,
// AND-composed server-side). Every number shown comes from a real DB
// aggregation endpoint — nothing is hardcoded:
//   5 KPI cards         -> /dashboard/kpis (paid-only net salary etc.)
//   Charts              -> /dashboard/salary-by-department, monthly trend
//   Payslip status      -> /dashboard/payslip-status
//   Payroll alerts      -> /dashboard/payroll-alerts
//   Attendance overview -> /dashboard/attendance-overview
//   Time off overview   -> /dashboard/time-off-overview (per-type rows)
//   Department overview -> /dashboard/salary-by-department (cost/headcount)
// All re-fetch together whenever the applied filters change.
import "../styles.css"
import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { Link } from "react-router-dom";

import {
  ApiError,
  getAttendanceOverview,
  getDashboardFilterOptions,
  getDashboardKpis,
  getMonthlyTrend,
  getPayrollAlerts,
  getPayslipStatus,
  getSalaryByDepartment,
  getTimeOffOverview,
  type DashboardFilters,
} from "../api/client";
import type {
  AttendanceOverview,
  DashboardFilterOptions,
  DashboardKpis,
  FilterOption,
  MonthlyTrendItem,
  PayrollAlertItem,
  PayslipStatusOverview,
  SalaryByDepartmentItem,
  TimeOffOverview,
} from "../api/types";
import { fmtMoney, fmtNum } from "../auth";

const EMPTY_FILTERS: DashboardFilters = {
  period_start: "",
  period_end: "",
  department_id: undefined,
  employee_type: "",
  company_id: undefined,
};

const MONTHS_LABEL = new Map<number, string>([
  [6, "6 months"],
  [12, "12 months"],
  [24, "24 months"],
]);

// ---------------------------------------------------------------------------
// Small presentational helpers
// ---------------------------------------------------------------------------

function MetricCard({
  label,
  value,
  hint,
}: {
  label: string;
  value: string;
  hint?: string;
}) {
  return (
    <div className="card metric">
      <b>{value}</b>
      <span>{label}</span>
      {hint ? <small className="muted">{hint}</small> : null}
    </div>
  );
}

function StatCell({
  label,
  value,
  tone,
}: {
  label: string;
  value: string | number;
  tone?: "ok" | "warn" | "bad" | "muted";
}) {
  const cls =
    tone === "ok"
      ? "badge badge-ok"
      : tone === "bad"
        ? "badge badge-absent"
        : tone === "warn"
          ? "badge badge-req-to_approve"
          : tone === "muted"
            ? "badge badge-muted"
            : "badge badge-present";
  return (
    <div className="card" style={{ textAlign: "center", padding: 12 }}>
      <span className={cls} style={{ fontSize: 18, padding: "4px 14px" }}>
        {value}
      </span>
      <div className="small muted" style={{ marginTop: 8 }}>
        {label}
      </div>
    </div>
  );
}

function Panel({
  title,
  children,
  right,
  hint,
}: {
  title: string;
  children: ReactNode;
  right?: ReactNode;
  hint?: string;
}) {
  return (
    <div className="card" style={{ padding: 16, marginTop: 12 }}>
      <div className="row spread" style={{ marginBottom: 10 }}>
        <h3 style={{ margin: 0, fontSize: 15 }}>{title}</h3>
        {right}
      </div>
      {hint ? <p className="small muted" style={{ marginTop: 0 }}>{hint}</p> : null}
      {children}
    </div>
  );
}

function TrendChart({ items }: { items: MonthlyTrendItem[] }) {
  // Minimal SVG line chart: even x spacing, 0..max y scale. Zero-filled
  // months come from the API so the line never has gaps.
  if (!items.length) return <p className="muted">No paid payruns yet.</p>;
  const max = Math.max(...items.map((i) => Number(i.total_net_salary)), 1);
  const W = 720;
  const H = 180;
  const PAD = 8;
  const step = (W - PAD * 2) / Math.max(items.length - 1, 1);
  const pts = items.map((it, idx) => ({
    x: PAD + idx * step,
    y: H - PAD - (Number(it.total_net_salary) / max) * (H - PAD * 2),
    item: it,
  }));
  const line = pts.map((p) => `${p.x},${p.y}`).join(" ");
  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", height: 180 }}>
      <line x1={PAD} y1={H - PAD} x2={W - PAD} y2={H - PAD} stroke="#dfe4ec" />
      {pts.map((p) => (
        <g key={p.item.month}>
          <circle cx={p.x} cy={p.y} r={3} fill="#2563eb" />
          <text
            x={p.x}
            y={H - 4}
            fontSize={9}
            fill="#8a94a6"
            textAnchor="middle"
          >
            {p.item.month.slice(2)}
          </text>
          <title>
            {p.item.month}: {fmtMoney(p.item.total_net_salary)}
          </title>
        </g>
      ))}
      <polyline
        points={line}
        fill="none"
        stroke="#2563eb"
        strokeWidth={2}
        strokeLinejoin="round"
      />
    </svg>
  );
}

const WARNING_LABEL = new Map<string, string>([
  ["missing_bank_details", "Missing bank account"],
  ["missing_contract", "Missing contract"],
  ["duplicate_payslip", "Duplicate payslip"],
  ["negative_net", "Negative net pay"],
  ["overlapping_period", "Overlapping payrun"],
  ["other", "Other payroll warning"],
]);

// ---------------------------------------------------------------------------
// Dashboard page
// ---------------------------------------------------------------------------

export function PayrollDashboardPage() {
  const [options, setOptions] = useState<DashboardFilterOptions>({
    companies: [],
    departments: [],
    employee_types: [],
  });
  // draft filter form vs applied filters (Apply commits the draft)
  const [draft, setDraft] = useState<DashboardFilters>({ ...EMPTY_FILTERS });
  const [filters, setFilters] = useState<DashboardFilters>({ ...EMPTY_FILTERS });

  const [kpis, setKpis] = useState<DashboardKpis | null>(null);
  const [dept, setDept] = useState<SalaryByDepartmentItem[]>([]);
  const [trend, setTrend] = useState<MonthlyTrendItem[]>([]);
  const [alerts, setAlerts] = useState<PayrollAlertItem[]>([]);
  const [openPayslips, setOpenPayslips] = useState(0);
  const [unvalidated, setUnvalidated] = useState(0);
  const [status, setStatus] = useState<PayslipStatusOverview | null>(null);
  const [att, setAtt] = useState<AttendanceOverview | null>(null);
  const [toff, setToff] = useState<TimeOffOverview | null>(null);
  const [months, setMonths] = useState(12);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  // Option lists (companies/departments/employee types) come from the API.
  const loadOptions = useCallback(async () => {
    try {
      setOptions(await getDashboardFilterOptions());
    } catch {
      /* non-fatal: the selects stay empty */
    }
  }, []);
  useEffect(() => {
    void loadOptions();
  }, [loadOptions]);

  const activeFilterCount =
    (filters.period_start ? 1 : 0) +
    (filters.period_end ? 1 : 0) +
    (filters.department_id ? 1 : 0) +
    (filters.employee_type ? 1 : 0) +
    (filters.company_id ? 1 : 0);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [k, d, t, a, at, to] = await Promise.all([
        getDashboardKpis(filters),
        getSalaryByDepartment(filters),
        getMonthlyTrend(months, filters),
        getPayrollAlerts(filters),
        getAttendanceOverview(filters),
        getTimeOffOverview(filters),
      ]);
      setKpis(k);
      setDept(d);
      setTrend(t);
      setAlerts(a.alerts);
      setOpenPayslips(a.total_open_payslips);
      setUnvalidated(a.unvalidated_payslips);
      setAtt(at);
      setToff(to);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setLoading(false);
    }
    // Payslip-status is fetched separately so a missing endpoint on an older
    // server build never blanks the rest of the dashboard panels.
    try {
      setStatus(await getPayslipStatus(filters));
    } catch {
      /* non-fatal: the "Payslip status" panel shows "No payslips" */
    }
  }, [filters, months]);

  useEffect(() => {
    void load();
  }, [load]);

  const maxDept = useMemo(
    () => Math.max(...dept.map((d) => Number(d.total_salary)), 1),
    [dept],
  );
  const maxDeptHeadcount = useMemo(
    () => Math.max(...dept.map((d) => d.headcount), 1),
    [dept],
  );

  function applyFilters() {
    setFilters({
      period_start: draft.period_start || undefined,
      period_end: draft.period_end || undefined,
      department_id: draft.department_id || undefined,
      employee_type: draft.employee_type || undefined,
      company_id: draft.company_id || undefined,
    });
  }

  function resetFilters() {
    setDraft({ ...EMPTY_FILTERS });
    setFilters({ ...EMPTY_FILTERS });
  }

  function setPreset(preset: "this-month" | "last-month" | "last-3-months") {
    const now = new Date();
    const iso = (d: Date) => {
      const pad = (n: number) => String(n).padStart(2, "0");
      return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
    };
    let from: Date;
    let to = new Date(now.getFullYear(), now.getMonth() + 1, 0);
    if (preset === "this-month") {
      from = new Date(now.getFullYear(), now.getMonth(), 1);
    } else if (preset === "last-month") {
      to = new Date(now.getFullYear(), now.getMonth(), 0);
      from = new Date(now.getFullYear(), now.getMonth() - 1, 1);
    } else {
      from = new Date(now.getFullYear(), now.getMonth() - 2, 1);
    }
    setDraft((prev) => ({ ...prev, period_start: iso(from), period_end: iso(to) }));
  }

  const activeDeptName =
    options.departments.find((d) => d.id === filters.department_id)?.name ?? "";
  const activeCompanyName =
    options.companies.find((c) => c.id === filters.company_id)?.name ?? "";

  return (
    <div>
      <div className="row spread">
        <h2>Payroll dashboard</h2>
        <div className="row-actions">
          <select value={months} onChange={(e) => setMonths(Number(e.target.value))}>
            {[...MONTHS_LABEL.entries()].map(([m, label]) => (
              <option key={m} value={m}>
                {label}
              </option>
            ))}
          </select>
        </div>
      </div>

      {/* ------------------------------------------------------------------ */}
      {/* Filter bar — every panel below re-queries with these filters */}
      {/* ------------------------------------------------------------------ */}
      <div className="card" style={{ padding: 14, marginTop: 8 }}>
        <div className="row spread" style={{ marginBottom: 8 }}>
          <b style={{ fontSize: 13 }}>Filters</b>
          <div className="row-actions">
            <button className="btn btn-ghost btn-sm" onClick={() => setPreset("this-month")}>
              This month
            </button>
            <button className="btn btn-ghost btn-sm" onClick={() => setPreset("last-month")}>
              Last month
            </button>
            <button className="btn btn-ghost btn-sm" onClick={() => setPreset("last-3-months")}>
              Last 3 months
            </button>
            <span className="muted small">period presets (adjust below)</span>
          </div>
        </div>
        <div className="form-grid" style={{ marginBottom: 0 }}>
          <label className="field">
            <span>Period from</span>
            <input
              type="date"
              value={draft.period_start}
              onChange={(e) => setDraft({ ...draft, period_start: e.target.value })}
            />
          </label>
          <label className="field">
            <span>Period to</span>
            <input
              type="date"
              value={draft.period_end}
              onChange={(e) => setDraft({ ...draft, period_end: e.target.value })}
            />
          </label>
          <label className="field">
            <span>Department</span>
            <select
              value={draft.department_id ?? ""}
              onChange={(e) =>
                setDraft({
                  ...draft,
                  department_id: e.target.value ? Number(e.target.value) : undefined,
                })
              }
            >
              <option value="">All departments</option>
              {options.departments.map((d) => (
                <option
                  key={d.id}
                  value={d.id}
                  disabled={
                    draft.company_id != null && d.company_id !== draft.company_id
                  }
                >
                  {d.name}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Employee type</span>
            <select
              value={draft.employee_type}
              onChange={(e) => setDraft({ ...draft, employee_type: e.target.value })}
            >
              <option value="">All types</option>
              {options.employee_types.map((t) => (
                <option key={t} value={t}>
                  {t.replace("_", " ")}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Company</span>
            <select
              value={draft.company_id ?? ""}
              onChange={(e) =>
                setDraft({
                  ...draft,
                  company_id: e.target.value ? Number(e.target.value) : undefined,
                })
              }
            >
              <option value="">All companies</option>
              {options.companies.map((c: FilterOption) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </label>
          <div className="row-actions" style={{ alignItems: "flex-end" }}>
            <button className="btn btn-primary" onClick={applyFilters}>
              Apply
            </button>
            <button className="btn btn-ghost" onClick={resetFilters}>
              Reset
            </button>
          </div>
        </div>
        <p className="small muted" style={{ margin: "8px 0 0" }}>
          {activeFilterCount === 0
            ? "No filters — showing all records (attendance sections default to the current calendar month)."
            : `Filtering by: ${[
                filters.period_start && filters.period_end
                  ? `period ${filters.period_start} → ${filters.period_end}`
                  : filters.period_start
                    ? `from ${filters.period_start}`
                    : filters.period_end
                      ? `until ${filters.period_end}`
                      : null,
                activeDeptName && `department: ${activeDeptName}`,
                filters.employee_type && `type: ${filters.employee_type.replace("_", " ")}`,
                activeCompanyName && `company: ${activeCompanyName}`,
              ]
                .filter(Boolean)
                .join(" · ")}`}
        </p>
      </div>

      {error ? <div className="alert alert-error">{error}</div> : null}
      {loading ? <p className="muted">Refreshing from database…</p> : null}

      {/* ------------------------------------------------------------------ */}
      {/* KPI row — 5 cards (paid-only net salary, generated, average, time */}
      {/* off approved, attendance health with the exact required caption) */}
      {/* ------------------------------------------------------------------ */}
      {kpis && (
        <div className="cards" style={{ marginTop: 12 }}>
          <MetricCard
            label="Total net salary paid"
            value={fmtMoney(kpis.total_net_salary_paid)}
            hint="paid payslips only"
          />
          <MetricCard
            label="Payslips generated"
            value={fmtNum(kpis.payslips_generated)}
          />
          <MetricCard
            label="Average salary"
            value={fmtMoney(kpis.average_salary)}
            hint="computed / validated / paid"
          />
          <MetricCard
            label="Approved time off days"
            value={fmtNum(Number(kpis.approved_time_off_days))}
            hint="approved day-unit requests in scope"
          />
          <MetricCard
            label="Attendance Health"
            value={`${kpis.attendance_health_pct.toFixed(1)}%`}
            hint="Present & on-time / expected days"
          />
        </div>
      )}

      {/* ------------------------------------------------------------------ */}
      {/* 2-Column Grid Layout: Panels side-by-side in card format            */}
      {/* ------------------------------------------------------------------ */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(450px, 1fr))",
          gap: 16,
          alignItems: "start",
        }}
      >
        {/* Left Column */}
        <div>
          <Panel
            title="Net salary by department"
            right={
              <span className="muted small">
                {fmtNum(dept.reduce((s, d) => s + d.headcount, 0))} employees · paid net
              </span>
            }
          >
            {dept.length === 0 ? (
              <p className="muted">No paid payslips match the filters.</p>
            ) : (
              <div className="stack">
                {dept.map((d) => (
                  <div key={d.department_name}>
                    <div className="row spread small">
                      <span>
                        <b>{d.department_name}</b>{" "}
                        <span className="muted">({fmtNum(d.headcount)})</span>
                      </span>
                      <span>{fmtMoney(d.total_salary)}</span>
                    </div>
                    <div
                      style={{
                        background: "#eef0f4",
                        borderRadius: 6,
                        height: 10,
                        marginTop: 4,
                      }}
                    >
                      <div
                        style={{
                          width: `${(Number(d.total_salary) / maxDept) * 100}%`,
                          background: "#2563eb",
                          borderRadius: 6,
                          height: 10,
                        }}
                      />
                    </div>
                  </div>
                ))}
              </div>
            )}
          </Panel>

          <Panel title="Payslip status">
            {!status ? (
              <p className="muted">No payslips match the filters.</p>
            ) : (
              <>
                <div className="cards">
                  <StatCell label="Paid" value={fmtNum(status.paid)} tone="ok" />
                  <StatCell label="Validated" value={fmtNum(status.validated)} tone="ok" />
                  <StatCell label="Computed" value={fmtNum(status.computed)} tone="warn" />
                  <StatCell label="Draft" value={fmtNum(status.draft)} tone="muted" />
                  <StatCell label="With warnings" value={fmtNum(status.with_warnings)} tone="bad" />
                  <StatCell label="Not validated" value={fmtNum(status.unvalidated)} tone="warn" />
                </div>
                {status.paid + status.validated + status.computed + status.draft > 0 && (
                  <div
                    style={{
                      background: "#eef0f4",
                      borderRadius: 6,
                      height: 8,
                      marginTop: 12,
                      display: "flex",
                      overflow: "hidden",
                    }}
                  >
                    {[
                      { label: "paid", v: status.paid, c: "#12805c" },
                      { label: "validated", v: status.validated, c: "#67a68f" },
                      { label: "computed", v: status.computed, c: "#e8943a" },
                      { label: "draft", v: status.draft, c: "#9aa4b2" },
                    ].map((s) => {
                      const total =
                        status.paid + status.validated + status.computed + status.draft;
                      return s.v > 0 ? (
                        <div
                          key={s.label}
                          title={`${s.label}: ${s.v}`}
                          style={{ width: `${(s.v / total) * 100}%`, background: s.c }}
                        />
                      ) : null;
                    })}
                  </div>
                )}
              </>
            )}
          </Panel>

          <Panel
            title="Attendance overview"
            right={
              <span className="muted small">
                {att ? `coverage ${att.coverage_pct.toFixed(1)}%` : ""}
              </span>
            }
          >
            {!att ||
            att.present + att.late + att.absent + att.overtime + att.missing_checkouts ===
              0 ? (
              <p className="muted">No attendance rows for the selected period.</p>
            ) : (
              <div className="cards">
                <StatCell label="Present" value={fmtNum(att.present)} tone="ok" />
                <StatCell label="Late" value={fmtNum(att.late)} tone="warn" />
                <StatCell label="Absent (derived)" value={fmtNum(att.absent)} tone="bad" />
                <StatCell label="Overtime" value={fmtNum(att.overtime)} />
                <StatCell
                  label="Missing check-outs"
                  value={fmtNum(att.missing_checkouts)}
                  tone="warn"
                />
                <StatCell label="Manual edits" value={fmtNum(att.manual_edits)} tone="muted" />
              </div>
            )}
          </Panel>
        </div>

        {/* Right Column */}
        <div>
          <Panel
            title="Monthly net salary (paid)"
            right={<span className="muted small">draft/computed excluded</span>}
          >
            <TrendChart items={trend} />
          </Panel>

          <Panel title="Payroll alerts">
            {alerts.length === 0 && openPayslips === 0 && unvalidated === 0 ? (
              <p className="muted">No open warnings — every payslip is clean.</p>
            ) : (
              <>
                <p className="small muted" style={{ marginTop: 0 }}>
                  {openPayslips} open payslip(s) carry warnings · {fmtNum(unvalidated)}{" "}
                  payslip(s) are not yet validated. All rows are generated from
                  live payslip / warning records.
                </p>
                <table className="table">
                  <thead>
                    <tr>
                      <th>Alert</th>
                      <th>Payslips</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {unvalidated > 0 && (
                      <tr>
                        <td>
                          <span className="badge badge-warn">Unvalidated payslip</span>
                        </td>
                        <td>{fmtNum(unvalidated)}</td>
                        <td />
                      </tr>
                    )}
                    {alerts.map((a) => (
                      <tr key={a.warning_type}>
                        <td>
                          <span className={`badge badge-warn-${a.warning_type}`}>
                            {WARNING_LABEL.get(a.warning_type) ??
                              a.warning_type.replaceAll("_", " ")}
                          </span>
                        </td>
                        <td>{fmtNum(a.count)}</td>
                        <td className="small muted">
                          {a.payslip_ids.length > 0
                            ? a.payslip_ids.slice(0, 3).map((id, i) => (
                                <span key={id}>
                                  {i > 0 ? ", " : ""}
                                  <Link to={`/payroll/payslips/${id}`}>#{id}</Link>
                                </span>
                              ))
                            : ""}
                          {a.payslip_ids.length > 3 ? ` +${a.payslip_ids.length - 3}` : ""}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </>
            )}
          </Panel>

          <Panel
            title="Time off overview"
            hint="Approved days / pending requests respect the period filter; remaining balances are the live current balances of the filtered employees."
            right={
              <span className="muted small">
                {fmtNum(Number(toff?.approved_days ?? 0))} approved ·{" "}
                {fmtNum(toff?.pending_requests ?? 0)} pending
              </span>
            }
          >
            {!toff || toff.by_type.length === 0 ? (
              <p className="muted">No time-off types with activity for the filters.</p>
            ) : (
              <table className="table">
                <thead>
                  <tr>
                    <th>Time off type</th>
                    <th>Approved days</th>
                    <th>Pending requests</th>
                    <th>Remaining balance</th>
                  </tr>
                </thead>
                <tbody>
                  {toff.by_type.map((t) => (
                    <tr key={t.time_off_type_name}>
                      <td>
                        <b>{t.time_off_type_name}</b>
                      </td>
                      <td>{fmtNum(Number(t.approved_days))}</td>
                      <td>
                        {t.pending_requests > 0 ? (
                          <span className="badge badge-req-to_approve">
                            {t.pending_requests}
                          </span>
                        ) : (
                          <span className="muted">—</span>
                        )}
                      </td>
                      <td>{fmtNum(Number(t.remaining))}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Panel>
        </div>
      </div>

      {/* Full-width Department Overview panel at the bottom */}
      <Panel title="Department overview">
        {dept.length === 0 ? (
          <p className="muted">No department payroll data for the filters.</p>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>Department</th>
                <th>Headcount</th>
                <th>Payroll cost (paid net)</th>
              </tr>
            </thead>
            <tbody>
              {dept.map((d) => (
                <tr key={d.department_name}>
                  <td>
                    <b>{d.department_name}</b>
                  </td>
                  <td>
                    <div className="row" style={{ gap: 8 }}>
                      {fmtNum(d.headcount)}
                      <div style={{ flex: 1, minWidth: 80 }}>
                        <div
                          style={{
                            background: "#eef0f4",
                            borderRadius: 6,
                            height: 6,
                            marginTop: 6,
                          }}
                        >
                          <div
                            style={{
                              width: `${(d.headcount / maxDeptHeadcount) * 100}%`,
                              background: "#12805c",
                              borderRadius: 6,
                              height: 6,
                            }}
                          />
                        </div>
                      </div>
                    </div>
                  </td>
                  <td>
                    <b>{fmtMoney(d.total_salary)}</b>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>
    </div>
  );
}
