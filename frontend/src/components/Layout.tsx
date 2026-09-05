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
  const { user, logout, isHr, hasRole } = useAuth();

  const isPayroll = hasRole("HR_PAYROLL_USER", "HR_PAYROLL_MANAGER", "ADMIN");
  const isEmployee = hasRole("EMPLOYEE");

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">
            P
          </span>
          PeoplePay360
        </div>
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
              title="Employee directory (wraps Ameen's API)"
            />
          )}
          {isHr && <NavItem to="/contracts" label="Contracts" />}
          {isHr && <NavItem to="/working-schedules" label="Working Schedules" />}
          {isEmployee && !isPayroll && <NavItem to="/payroll/payslips" label="My Payslips" />}
          {hasRole("ADMIN") && (
            <>
              <NavItem
                to="/accounts"
                label="Accounts"
                title="Provision + manage login accounts (ADMIN only)"
              />
              <NavItem to="/admin" label="Admin" />
            </>
          )}
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

      {/* Payroll sub-navigation (payroll roles only) */}
      {isPayroll && (
        <nav className="subnav">
          <span className="subnav-label">Payroll</span>
          <NavItem to="/payroll/payruns" label="Payruns" />
          <NavItem to="/payroll/payslips" label="Payslips" />
          <NavItem to="/payroll/dashboard" label="Dashboard" />
          <span className="subnav-sep" />
          <span className="subnav-label">Config</span>
          <NavItem to="/payroll/structures" label="Salary Structures" />
          <NavItem to="/payroll/rules" label="Salary Rules" />
        </nav>
      )}

      <main className="content">
        <Outlet />
      </main>
    </div>
  );
}