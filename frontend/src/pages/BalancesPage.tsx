import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import "../styles.css"
import { ApiError, listBalances, listMyBalances } from "../api/client";
import type { TimeOffBalance } from "../api/types";
import { fmtNum, useAuth } from "../auth";

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

// Deterministic color per leave type — known types get an intentional hue,
// anything unrecognized still gets a consistent (not random-per-render) color.
function typeBadgeClass(name: string): string {
  const key = name.trim().toLowerCase();
  if (key.includes("paid time off") || key === "pto") return "badge-leave-pto";
  if (key.includes("sick")) return "badge-leave-sick";
  if (key.includes("work from home") || key === "wfh") return "badge-leave-wfh";
  if (key.includes("unpaid")) return "badge-leave-unpaid";
  if (key.includes("parental") || key.includes("maternity") || key.includes("paternity")) {
    return "badge-leave-parental";
  }

  const palette = [
    "badge-leave-pto",
    "badge-leave-sick",
    "badge-leave-wfh",
    "badge-leave-unpaid",
    "badge-leave-parental",
    "badge-leave-other",
  ];
  let hash = 0;
  for (let i = 0; i < key.length; i++) hash = (hash * 31 + key.charCodeAt(i)) >>> 0;
  return palette[hash % palette.length];
}

function usageTone(pct: number): "ok" | "warn" | "danger" {
  if (pct >= 80) return "danger";
  if (pct >= 50) return "warn";
  return "ok";
}

function TypeBadge({ name }: { name: string }) {
  return (
    <span
      className={`badge ${typeBadgeClass(name)}`}
      style={{ padding: "4px 10px", fontSize: "12px", fontWeight: 600, display: "inline-block" }}
    >
      {name}
    </span>
  );
}

function UsageBar({ pct }: { pct: number }) {
  const tone = usageTone(pct);
  return (
    <div className="row" style={{ gap: 8, alignItems: "center" }}>
      <div className="bar-track" style={{ flex: 1 }}>
        <div className={`bar-fill bar-fill-${tone}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="small muted" style={{ width: 36, textAlign: "right" }}>
        {pct}%
      </span>
    </div>
  );
}

export function BalancesPage() {
  const { isHr } = useAuth();
  const [rows, setRows] = useState<TimeOffBalance[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // Filters
  const [search, setSearch] = useState("");
  const [selectedType, setSelectedType] = useState("");
  const [viewMode, setViewMode] = useState<"table" | "cards">("table");

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      if (!isHr) {
        setRows(await listMyBalances());
      } else {
        setRows(await listBalances());
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load time off balances.");
    } finally {
      setLoading(false);
    }
  }, [isHr]);

  useEffect(() => {
    void load();
  }, [load]);

  // Available type filter options
  const typeOptions = useMemo(() => {
    const types = new Set<string>();
    rows.forEach((r) => types.add(r.type_name));
    return Array.from(types).sort();
  }, [rows]);

  // Filtered rows
  const filteredRows = useMemo(() => {
    return rows.filter((r) => {
      const empName = (r.employee_name ?? `#${r.employee_id}`).toLowerCase();
      const matchesSearch =
        !search.trim() ||
        empName.includes(search.toLowerCase().trim()) ||
        String(r.employee_id).includes(search.trim());

      const matchesType = !selectedType || r.type_name === selectedType;

      return matchesSearch && matchesType;
    });
  }, [rows, search, selectedType]);

  // Calculated KPI stats
  const stats = useMemo(() => {
    const uniqueEmployees = new Set(rows.map((r) => r.employee_id)).size;
    let totalAllocated = 0;
    let totalTaken = 0;
    let totalRemaining = 0;

    filteredRows.forEach((r) => {
      totalAllocated += Number(r.allocated) || 0;
      totalTaken += Number(r.taken) || 0;
      totalRemaining += Number(r.remaining) || 0;
    });

    return {
      employeeCount: uniqueEmployees,
      allocated: totalAllocated,
      taken: totalTaken,
      remaining: totalRemaining,
    };
  }, [rows, filteredRows]);

  return (
    <div className="stack" style={{ gap: 20 }}>
      {/* Page Header */}
      <div className="row spread" style={{ alignItems: "center" }}>
        <div>
          <h2 style={{ margin: 0, fontSize: 22, fontWeight: 700 }}>
            {isHr ? "Time off balances — all employees" : "My time off balances"}
          </h2>
          <p className="muted small" style={{ margin: "4px 0 0" }}>
            Live calculated leave allocations, usage, and remaining balances across your team.
          </p>
        </div>
        {isHr && (
          <div className="row-actions">
            <button
              className={`btn btn-sm ${viewMode === "table" ? "btn-primary" : "btn-ghost"}`}
              onClick={() => setViewMode("table")}
            >
              Table view
            </button>
            <button
              className={`btn btn-sm ${viewMode === "cards" ? "btn-primary" : "btn-ghost"}`}
              onClick={() => setViewMode("cards")}
            >
              Card view
            </button>
          </div>
        )}
      </div>

      {error && <div className="alert alert-error">{error}</div>}

      {/* KPI Metric Summary Row */}
      <div className="cards" style={{ gap: 12 }}>
        {isHr && (
          <MetricCard
            label="Employees"
            value={fmtNum(stats.employeeCount)}
            hint="Staff with active allocations"
          />
        )}
        <MetricCard
          label="Total Allocated"
          value={`${fmtNum(stats.allocated)} days`}
          hint="Approved leave grants"
        />
        <MetricCard
          label="Total Taken"
          value={`${fmtNum(stats.taken)} days`}
          hint="Approved leave requests"
        />
        {/* the headline number people actually opened this page to check */}
        <MetricCard
          label="Net Remaining"
          value={`${fmtNum(stats.remaining)} days`}
          hint="Available to take"
          tone="ok"
        />
      </div>

      {/* Filter and Search Bar */}
      <div className="card" style={{ padding: 14 }}>
        <div className="form-grid" style={{ marginBottom: 0 }}>
          {isHr && (
            <label className="field">
              <span>Search employee</span>
              <input
                type="text"
                placeholder="Search name or ID…"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </label>
          )}

          <label className="field">
            <span>Filter leave type</span>
            <select value={selectedType} onChange={(e) => setSelectedType(e.target.value)}>
              <option value="">All leave types</option>
              {typeOptions.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </label>

          {(search || selectedType) && (
            <div className="row-actions" style={{ alignItems: "flex-end" }}>
              <button
                className="btn btn-ghost btn-sm"
                onClick={() => {
                  setSearch("");
                  setSelectedType("");
                }}
              >
                Reset filters
              </button>
            </div>
          )}
        </div>
      </div>

      {/* Main Content View */}
      {loading ? (
        <div className="card" style={{ padding: 24, textAlign: "center" }}>
          <p className="muted">Loading live leave balances…</p>
        </div>
      ) : filteredRows.length === 0 ? (
        <div className="card empty-state">
          <p className="muted" style={{ fontSize: 15 }}>
            No leave balance records found.
          </p>
          {(search || selectedType) && (
            <small className="muted">Try adjusting your filters above.</small>
          )}
        </div>
      ) : viewMode === "cards" ? (
        /* Card Layout View */
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(320px, 1fr))",
            gap: 16,
          }}
        >
          {filteredRows.map((b) => {
            const alloc = Number(b.allocated) || 0;
            const taken = Number(b.taken) || 0;
            const pctUsed = alloc > 0 ? Math.min(100, Math.round((taken / alloc) * 100)) : 0;

            return (
              <div key={`${b.employee_id}-${b.time_off_type_id}`} className="card" style={{ padding: 16 }}>
                <div className="row spread" style={{ marginBottom: 10, alignItems: "flex-start" }}>
                  <div>
                    <b style={{ fontSize: 15, display: "block", color: "#111827" }}>
                      {b.employee_name ?? `Employee #${b.employee_id}`}
                    </b>
                    <span className="muted small">ID #{b.employee_id}</span>
                  </div>
                  <TypeBadge name={b.type_name} />
                </div>

                {/* Progress bar */}
                <div style={{ margin: "12px 0 16px" }}>
                  <div className="row spread small muted" style={{ marginBottom: 4 }}>
                    <span>Usage ({pctUsed}%)</span>
                    <span>
                      {b.taken} / {b.allocated} {b.unit}
                    </span>
                  </div>
                  <UsageBar pct={pctUsed} />
                </div>

                {/* Balance breakdown cells */}
                <div className="row spread" style={{ background: "#f8fafc", padding: "10px 12px", borderRadius: 8 }}>
                  <div style={{ textAlign: "center" }}>
                    <span className="small muted" style={{ display: "block" }}>Allocated</span>
                    <b>{b.allocated}</b>
                  </div>
                  <div style={{ textAlign: "center" }}>
                    <span className="small muted" style={{ display: "block" }}>Taken</span>
                    <span>{b.taken}</span>
                  </div>
                  <div style={{ textAlign: "center" }}>
                    <span className="small muted" style={{ display: "block" }}>Remaining</span>
                    <b className={`balance-remaining-${usageTone(pctUsed)}`} style={{ fontSize: 16 }}>
                      {b.remaining}
                    </b>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      ) : (
        /* Table Layout View */
        <div className="card" style={{ padding: 0, overflow: "hidden" }}>
          <table className="table" style={{ margin: 0 }}>
            <thead>
              <tr>
                {isHr && <th>Employee</th>}
                <th>Leave Type</th>
                <th>Unit</th>
                <th style={{ textAlign: "right" }}>Allocated</th>
                <th style={{ textAlign: "right" }}>Taken</th>
                <th style={{ textAlign: "right" }}>Remaining</th>
                <th style={{ width: 160 }}>Usage</th>
              </tr>
            </thead>
            <tbody>
              {filteredRows.map((b, idx) => {
                const alloc = Number(b.allocated) || 0;
                const taken = Number(b.taken) || 0;
                const pctUsed = alloc > 0 ? Math.min(100, Math.round((taken / alloc) * 100)) : 0;
                const prevRow = filteredRows[idx - 1];
                const isNewGroup = isHr && idx !== 0 && (!prevRow || prevRow.employee_id !== b.employee_id);

                return (
                  <tr
                    key={`${b.employee_id}-${b.time_off_type_id}`}
                    className={isNewGroup ? "row-group-start" : undefined}
                  >
                    {isHr && (
                      <td>
                        <Link to={`/employees`} style={{ fontWeight: 600, textDecoration: "none" }}>
                          {b.employee_name ?? `Employee #${b.employee_id}`}
                        </Link>
                        <div className="small muted">ID #{b.employee_id}</div>
                      </td>
                    )}
                    <td>
                      <TypeBadge name={b.type_name} />
                    </td>
                    <td>
                      <span className="unit-tag">{b.unit}</span>
                    </td>
                    <td style={{ textAlign: "right", fontWeight: 500 }}>{b.allocated}</td>
                    <td style={{ textAlign: "right", color: taken > 0 ? "#4b5563" : "#9ca3af" }}>
                      {b.taken}
                    </td>
                    <td style={{ textAlign: "right" }}>
                      <span className={`balance-remaining balance-remaining-${usageTone(pctUsed)}`}>
                        {b.remaining} {b.unit}
                      </span>
                    </td>
                    <td>
                      <UsageBar pct={pctUsed} />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}