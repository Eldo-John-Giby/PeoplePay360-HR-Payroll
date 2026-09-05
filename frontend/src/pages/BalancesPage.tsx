import { useCallback, useEffect, useState } from "react";

import { ApiError, listBalances, listMyBalances } from "../api/client";
import type { TimeOffBalance } from "../api/types";
import { useAuth } from "../auth";

function BalanceTable({ rows }: { rows: TimeOffBalance[] }) {
  if (rows.length === 0) return <div className="muted">No balances yet.</div>;
  return (
    <table className="table">
      <thead>
        <tr>
          <th>Employee</th>
          <th>Type</th>
          <th>Unit</th>
          <th>Allocated</th>
          <th>Taken</th>
          <th>Remaining</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((b) => (
          <tr key={`${b.employee_id}-${b.time_off_type_id}`}>
            <td>{b.employee_name ?? `#${b.employee_id}`}</td>
            <td>{b.type_name}</td>
            <td>{b.unit}</td>
            <td>{b.allocated}</td>
            <td>{b.taken}</td>
            <td><b>{b.remaining}</b></td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function BalancesPage() {
  const { isHr } = useAuth();
  const [rows, setRows] = useState<TimeOffBalance[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [employeeId, setEmployeeId] = useState("");

  const load = useCallback(async () => {
    setError(null);
    try {
      if (!isHr) {
        setRows(await listMyBalances());
        return;
      }
      const id = employeeId.trim() === "" ? undefined : Number(employeeId.trim());
      setRows(await listBalances(Number.isNaN(id as number) ? undefined : id));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load balances.");
    }
  }, [isHr, employeeId]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="stack">
      <div className="row spread">
        <h2>{isHr ? "Time off balances - all employees" : "My time off balances"}</h2>
        {isHr && (
          <input
            type="number"
            placeholder="Filter by employee id…"
            value={employeeId}
            onChange={(e) => setEmployeeId(e.target.value)}
          />
        )}
      </div>
      {error && <div className="alert alert-error">{error}</div>}
      <p className="muted small">
        Balances are computed live from approved allocations minus approved requests -
        never a stored total.
      </p>
      <BalanceTable rows={rows} />
    </div>
  );
}
