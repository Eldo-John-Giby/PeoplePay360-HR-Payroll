import { NavLink, Outlet } from "react-router-dom";

import { useAuth } from "../auth";

function NavItem({
  to,
  label,
  disabled = false,
  title,
}: {
  to: string;
  label: string;
  disabled?: boolean;
  title?: string;
}) {
  if (disabled) {
    return (
      <span className="nav-item nav-disabled" title={title}>
        {label}
      </span>
    );
  }
  return (
    <NavLink
      to={to}
      className={({ isActive }) => `nav-item${isActive ? " active" : ""}`}
    >
      {label}
    </NavLink>
  );
}

export function Layout() {
  const { user, logout, isHr } = useAuth();

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">PeoplePay360</div>
        <nav className="nav">
          <NavItem to="/attendance" label="Attendance" />
          <NavItem to="/time-off/requests" label="Time Off Requests" />
          <NavItem to="/time-off/balances" label="My Balances" />
          {isHr && <NavItem to="/time-off/allocations" label="Allocations" />}
          <NavItem to="/time-off/types" label="Time Off Types" />
          {isHr && (
            <NavItem
              to="/employees"
              label="Employees"
              title="Employee directory (wraps Ameen's API — lights up when his slice merges)"
            />
          )}
          <NavItem
            to="/payroll"
            label="Payroll"
            disabled
            title="Payroll screens wire in once Steve's endpoints stabilize"
          />
        </nav>
        <div className="userbox">
          <div className="user-meta">
            <span className="user-name">{user?.employee?.full_name ?? user?.email}</span>
            <span className="user-roles">
              {user?.roles.map((r) => r.name).join(", ")}
            </span>
          </div>
          <button className="btn btn-ghost" onClick={logout}>
            Logout
          </button>
        </div>
      </header>
      <main className="content">
        <Outlet />
      </main>
    </div>
  );
}
