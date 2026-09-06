import { useCallback, useEffect, useState } from "react";
import "../styles.css"
import { ApiError, fetchUsers, updateUserRoles } from "../api/client";
import type { UserOut } from "../api/types";
import { useAuth } from "../auth";

const ALL_ROLES = [
  "EMPLOYEE",
  "HR_MANAGER",
  "HR_PAYROLL_USER",
  "HR_PAYROLL_MANAGER",
  "ADMIN",
];

export function AdminPage() {
  const { hasRole, user: me } = useAuth();
  const isAdmin = hasRole("ADMIN");

  const [users, setUsers] = useState<UserOut[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      setUsers(await fetchUsers());
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load users.");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  if (!isAdmin) {
    return <div className="alert alert-error">This page is visible to ADMIN only.</div>;
  }

  async function changeRole(userId: number, roleName: string) {
    setError(null);
    setNotice(null);
    setBusyId(userId);
    try {
      await updateUserRoles(userId, [roleName]);
      setNotice("Role updated.");
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to update role.");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="stack">
      <h2>Admin / Settings</h2>
      {error && <div className="alert alert-error">{error}</div>}
      {notice && <div className="alert alert-ok">{notice}</div>}

      <div className="card">
        <h3>User accounts</h3>
        <p className="muted small" style={{ marginBottom: 10 }}>
          Change a user&apos;s role via the dropdown. You cannot demote your own ADMIN
          account (the backend blocks it).
        </p>
        <table className="table">
          <thead>
            <tr>
              <th>Email</th>
              <th>Linked employee</th>
              <th>Role</th>
              <th>Status</th>
              <th>Change role</th>
            </tr>
          </thead>
          <tbody>
            {users.map((u) => {
              const current = u.roles[0]?.name ?? "-";
              return (
                <tr key={u.id}>
                  <td><b>{u.email}</b></td>
                  <td>{u.employee?.full_name ?? <span className="muted">not linked</span>}</td>
                  <td>
                    <span className={`badge ${current === "ADMIN" ? "badge-overtime" : current === "EMPLOYEE" ? "badge-ok" : "badge-alloc-to_approve"}`}>
                      {current}
                    </span>
                  </td>
                  <td>
                    {u.is_active ? (
                      <span className="badge badge-ok">Active</span>
                    ) : (
                      <span className="badge badge-req-cancelled">Inactive</span>
                    )}
                  </td>
                  <td className="row-actions">
                    <select
                      value={current}
                      disabled={busyId === u.id || u.id === me?.id}
                      onChange={(e) => void changeRole(u.id, e.target.value)}
                      title={u.id === me?.id ? "You cannot change your own role" : "Assign role"}
                    >
                      {ALL_ROLES.map((r) => (
                        <option key={r} value={r}>{r}</option>
                      ))}
                    </select>
                    {busyId === u.id && <span className="muted small">saving…</span>}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}