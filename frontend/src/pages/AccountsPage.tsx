import { useCallback, useEffect, useState, type FormEvent } from "react";

import {
  ApiError,
  createUser,
  fetchUsers,
  listEmployees,
  updateUser,
} from "../api/client";
import type { UserOut } from "../api/types";

// The five seeded roles. Accounts default to EMPLOYEE (least privilege);
// HR/payroll/admin roles are granted deliberately.
const ROLES = [
  { name: "EMPLOYEE", desc: "Self-service attendance & own leave only" },
  { name: "HR_MANAGER", desc: "Full HR CRUD + approve/refuse; no payroll" },
  { name: "HR_PAYROLL_USER", desc: "HR + payroll processing" },
  { name: "HR_PAYROLL_MANAGER", desc: "HR + payroll approval" },
  { name: "ADMIN", desc: "Everything, incl. managing accounts" },
];

interface EmployeeOpt {
  id: number;
  full_name: string;
  work_email: string | null;
}

export function AccountsPage() {
  // ---- create form --------------------------------------------------------
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [role, setRole] = useState("EMPLOYEE");
  const [linkOnCreate, setLinkOnCreate] = useState("");
  const [active, setActive] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  // ---- directory of existing accounts + linkable employees ---------------
  const [users, setUsers] = useState<UserOut[]>([]);
  const [employees, setEmployees] = useState<EmployeeOpt[]>([]);
  const [directoryDown, setDirectoryDown] = useState(false);
  const [loading, setLoading] = useState(true);
  // Draft "link employee" selection per user row (keyed by user id).
  const [drafts, setDrafts] = useState<Record<number, string>>({});
  const [rowBusy, setRowBusy] = useState<number | null>(null);
  const [rowError, setRowError] = useState<Record<number, string>>({});

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const [empPage, userList] = await Promise.all([
        listEmployees(),
        fetchUsers(),
      ]);
      setEmployees(
        empPage
          ? (empPage.items
              .map((e) => ({
                id: e.id,
                full_name: String(e.full_name ?? e.name ?? e.id),
                work_email: (e.work_email as string | null | undefined) ?? null,
              }))
              .sort((a, b) => a.full_name.localeCompare(b.full_name)) as EmployeeOpt[])
          : [],
      );
      if (!empPage) setDirectoryDown(true);
      setUsers(userList);
      const nextDrafts: Record<number, string> = {};
      for (const u of userList) {
        nextDrafts[u.id] = u.employee_id !== null ? String(u.employee_id) : "";
      }
      setDrafts(nextDrafts);
    } catch {
      setDirectoryDown(true);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  /** Employees a given user may be linked to: unlinked employees plus (so the
   * row always shows its current link) the employee they already own. */
  function linkOptions(user: UserOut): EmployeeOpt[] {
    const takenByOthers = new Set(
      users
        .filter((u) => u.id !== user.id)
        .map((u) => u.employee_id)
        .filter((v): v is number => v !== null),
    );
    return employees.filter((e) => !takenByOthers.has(e.id));
  }

  // ---- create --------------------------------------------------------------

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSuccess(null);
    if (password.length < 8) {
      setError("Password must be at least 8 characters.");
      return;
    }
    if (password !== confirm) {
      setError("Passwords do not match.");
      return;
    }
    setBusy(true);
    try {
      const created = await createUser({
        email,
        password,
        role_names: [role],
        employee_id: linkOnCreate ? Number(linkOnCreate) : null,
        is_active: active,
      });
      setSuccess(
        `Account ${created.email} created${created.employee ? ` and linked to ${created.employee.full_name}` : " (unlinked)"}.`,
      );
      setEmail("");
      setPassword("");
      setConfirm("");
      setRole("EMPLOYEE");
      setLinkOnCreate("");
      await reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not create account.");
    } finally {
      setBusy(false);
    }
  }

  // ---- per-row updates -----------------------------------------------------

  async function saveLink(userId: number) {
    const target = Number(drafts[userId] ?? "");
    const user = users.find((u) => u.id === userId);
    if (!user) return;
    const current = user.employee_id ?? null;
    const next = drafts[userId] ? target : null;
    if (next === current) return;
    setRowBusy(userId);
    setRowError((prev) => ({ ...prev, [userId]: "" }));
    try {
      const updated = await updateUser(userId, { employee_id: next });
      setUsers((prev) => prev.map((u) => (u.id === userId ? updated : u)));
    } catch (err) {
      setRowError((prev) => ({
        ...prev,
        [userId]: err instanceof ApiError ? err.message : "Update failed.",
      }));
    } finally {
      setRowBusy(null);
    }
  }

  async function toggleActive(user: UserOut) {
    setRowBusy(user.id);
    setRowError((prev) => ({ ...prev, [user.id]: "" }));
    try {
      const updated = await updateUser(user.id, { is_active: !user.is_active });
      setUsers((prev) => prev.map((u) => (u.id === user.id ? updated : u)));
    } catch (err) {
      setRowError((prev) => ({
        ...prev,
        [user.id]: err instanceof ApiError ? err.message : "Update failed.",
      }));
    } finally {
      setRowBusy(null);
    }
  }

  return (
    <div>
      <h2>Accounts</h2>
      <p className="muted">
        Provision and manage logins (ADMIN only — there is no public
        self-signup; HR hands out credentials). An EMPLOYEE account must be
        linked to an employee profile before attendance self-service works —
        link it here or at creation.
      </p>

      {success && <div className="alert alert-success">{success}</div>}

      <form className="card" onSubmit={onSubmit}>
        <h3>Create account</h3>
        <div className="form-grid">
          <label className="field">
            <span>Work email *</span>
            <input
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="name@oxp.com"
            />
          </label>

          <label className="field">
            <span>Password *</span>
            <input
              type="password"
              required
              autoComplete="new-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="At least 8 characters"
            />
          </label>

          <label className="field">
            <span>Confirm password *</span>
            <input
              type="password"
              required
              autoComplete="new-password"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              placeholder="Repeat the password"
            />
          </label>

          <label className="field">
            <span>Role *</span>
            <select value={role} onChange={(e) => setRole(e.target.value)}>
              {ROLES.map((r) => (
                <option key={r.name} value={r.name}>
                  {r.name} — {r.desc}
                </option>
              ))}
            </select>
          </label>

          <label className="field">
            <span>Link employee (optional)</span>
            {directoryDown ? (
              <input
                type="number"
                min={1}
                value={linkOnCreate}
                onChange={(e) => setLinkOnCreate(e.target.value)}
                placeholder="Directory unavailable — enter id"
              />
            ) : (
              <select
                value={linkOnCreate}
                onChange={(e) => setLinkOnCreate(e.target.value)}
              >
                <option value="">None (leave unlinked for now)</option>
                {employees.map((emp) => (
                  <option key={emp.id} value={emp.id}>
                    {emp.full_name}
                    {emp.work_email ? ` (${emp.work_email})` : ""}
                  </option>
                ))}
              </select>
            )}
          </label>

          <label className="check field-wide">
            <input
              type="checkbox"
              checked={active}
              onChange={(e) => setActive(e.target.checked)}
            />
            Account is active (can sign in immediately)
          </label>
        </div>

        {error && <div className="alert alert-error">{error}</div>}

        <button className="btn btn-primary" disabled={busy}>
          {busy ? "Creating…" : "Create account"}
        </button>
      </form>

      <div className="card" style={{ marginTop: 16 }}>
        <h3>Existing accounts ({users.length})</h3>
        {loading ? (
          <div className="muted">Loading…</div>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>Email</th>
                <th>Roles</th>
                <th>Linked employee</th>
                <th>Link / change</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {users.map((u) => {
                const opts = linkOptions(u);
                const dirty = (drafts[u.id] ? Number(drafts[u.id]) : null) !==
                  (u.employee_id ?? null);
                return (
                  <tr key={u.id}>
                    <td>
                      {u.email}
                      {rowError[u.id] && (
                        <div className="alert alert-error row-alert">
                          {rowError[u.id]}
                        </div>
                      )}
                    </td>
                    <td>{u.roles.map((r) => r.name).join(", ")}</td>
                    <td>{u.employee?.full_name ?? <span className="muted">not linked</span>}</td>
                    <td>
                      <div className="row-actions">
                        <select
                          value={drafts[u.id] ?? ""}
                          onChange={(e) =>
                            setDrafts((prev) => ({
                              ...prev,
                              [u.id]: e.target.value,
                            }))
                          }
                          disabled={rowBusy === u.id}
                        >
                          <option value="">Unlink</option>
                          {opts.map((emp) => (
                            <option key={emp.id} value={emp.id}>
                              {emp.full_name}
                            </option>
                          ))}
                        </select>
                        <button
                          className="btn btn-ghost"
                          disabled={rowBusy === u.id || !dirty}
                          onClick={() => void saveLink(u.id)}
                        >
                          {rowBusy === u.id ? "Saving…" : "Save link"}
                        </button>
                      </div>
                    </td>
                    <td>
                      <button
                        className="btn btn-ghost"
                        disabled={rowBusy === u.id}
                        onClick={() => void toggleActive(u)}
                        title={
                          u.is_active
                            ? "Disable login for this account"
                            : "Enable login for this account"
                        }
                      >
                        {u.is_active ? "Active ✓" : "Disabled"}
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
        {directoryDown && (
          <p className="muted">
            Employee directory unavailable — link by employee id is not shown.
          </p>
        )}
      </div>
    </div>
  );
}
