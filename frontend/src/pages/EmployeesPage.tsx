import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import "../styles.css"
import { ApiError, listEmployees } from "../api/client";
import type { EmployeeListItem } from "../api/types";
import { fmtDate, useAuth } from "../auth";

type Tab = "all" | "active" | "inactive" | "terminated";

const TABS: { key: Tab; label: string }[] = [
  { key: "all", label: "All" },
  { key: "active", label: "Active" },
  { key: "inactive", label: "Inactive" },
  { key: "terminated", label: "Terminated" },
];

const TYPE_BADGE: Record<string, string> = {
  full_time: "Full-time",
  part_time: "Part-time",
  contract: "Contract",
  intern: "Intern",
};

export function EmployeesPage() {
  useAuth();
  const [employees, setEmployees] = useState<EmployeeListItem[]>([]);
  const [apiUp, setApiUp] = useState<boolean | null>(null); // null = loading
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("all");
  const [query, setQuery] = useState("");
  const [selectedId, setSelectedId] = useState<number | null>(null);

  const load = useCallback(async () => {
    setError(null);
    setApiUp(null);
    try {
      const page = await listEmployees();
      if (page === null) {
        setApiUp(false);
        setEmployees([]);
        return;
      }
      setApiUp(true);
      setEmployees(page.items);
    } catch (err) {
      setApiUp(false);
      setError(err instanceof ApiError ? err.message : "Failed to load employees.");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    return employees.filter((e) => {
      if (tab !== "all" && e.status !== tab) return false;
      if (!q) return true;
      const name = (e.full_name ?? "").toLowerCase();
      const email = (e.work_email ?? "").toLowerCase();
      return name.includes(q) || email.includes(q) || String(e.id) === q;
    });
  }, [employees, tab, query]);

  const selected = employees.find((e) => e.id === selectedId) ?? null;

  if (apiUp === false) {
    return (
      <div className="stack">
        <div className="row spread">
          <h2>Employee directory</h2>
          <button className="btn btn-ghost btn-sm" onClick={() => void load()}>
            Retry
          </button>
        </div>
        <div className="card">
          <h3>Ameen's employee API isn't live yet</h3>
          <p className="muted">
            This screen wraps <code>/api/v1/employees</code> from Ameen's slice. Until his
            router is merged this stays empty; the rest of the console (attendance + time
            off) runs against its own API and is unaffected.
          </p>
          {error && <div className="alert alert-error">{error}</div>}
        </div>
      </div>
    );
  }

  return (
    <div className="stack">
      <div className="row spread">
        <h2>Employee directory</h2>
        <input
          placeholder="Search name, email or id…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          style={{ minWidth: 260 }}
        />
      </div>

      <div className="row">
        {TABS.map((t) => (
          <button
            key={t.key}
            className={`btn btn-sm ${tab === t.key ? "btn-primary" : "btn-ghost"}`}
            onClick={() => setTab(t.key)}
          >
            {t.label}
          </button>
        ))}
      </div>

      <div className="row" style={{ alignItems: "flex-start" }}>
        <table className="table" style={{ flex: 1 }}>
          <thead>
            <tr>
              <th>Name</th>
              <th>Department / Role</th>
              <th>Type</th>
              <th>Status</th>
              <th>Joined</th>
            </tr>
          </thead>
          <tbody>
            {visible.map((e) => (
              <tr
                key={e.id}
                className={selectedId === e.id ? "row-selected" : undefined}
                onClick={() => setSelectedId(e.id)}
              >
                <td>
                  <b>{e.full_name}</b>
                  <div className="muted small">{e.work_email}</div>
                </td>
                <td>
                  {e.department_name ?? "-"}
                  <div className="muted small">{e.job_position_name ?? ""}</div>
                </td>
                <td>{e.employee_type ? (TYPE_BADGE[e.employee_type] ?? e.employee_type) : "-"}</td>
                <td>
                  <span className={`badge badge-${e.status ?? "active"}`}>
                    {e.status ?? "active"}
                  </span>
                </td>
                <td>{e.date_of_joining ? fmtDate(e.date_of_joining) : "-"}</td>
              </tr>
            ))}
            {visible.length === 0 && (
              <tr>
                <td colSpan={5} className="muted">
                  {apiUp === null ? "Loading…" : "No employees match."}
                </td>
              </tr>
            )}
          </tbody>
        </table>

        {selected && (
          <div className="card" style={{ width: 300 }}>
            <h3>{selected.full_name}</h3>
            <div className="muted small">{selected.work_email}</div>
            <dl className="kv">
              <dt>Department</dt>
              <dd>{selected.department_name ?? "-"}</dd>
              <dt>Role</dt>
              <dd>{selected.job_position_name ?? "-"}</dd>
              <dt>Type</dt>
              <dd>{selected.employee_type ?? "-"}</dd>
              <dt>Status</dt>
              <dd>{selected.status ?? "-"}</dd>
              <dt>Joined</dt>
              <dd>{selected.date_of_joining ? fmtDate(selected.date_of_joining) : "-"}</dd>
            </dl>
            <div className="row">
              <Link className="btn btn-primary btn-sm" to={`/attendance?employee=${selected.id}`}>
                View attendance
              </Link>
              <Link className="btn btn-ghost btn-sm" to={`/time-off/requests?employee=${selected.id}`}>
                Request leave for them
              </Link>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
