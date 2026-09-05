import { useEffect, useRef, useState, type ReactNode } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";

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

/** Dropdown nav item: a button that opens a menu of links; closes on outside
 *  click or when a link is followed. */
function NavDropdown({
  label,
  active,
  children,
}: {
  label: string;
  active: boolean;
  children: (close: () => void) => ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onDocClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("click", onDocClick);
    return () => document.removeEventListener("click", onDocClick);
  }, [open]);

  return (
    <div className="nav-dropdown" ref={ref}>
      <button
        type="button"
        className={`nav-item nav-dropdown-btn${active || open ? " active" : ""}`}
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
      >
        {label} <span className="caret">▾</span>
      </button>
      {open && <div className="dropdown-menu">{children(() => setOpen(false))}</div>}
    </div>
  );
}

function DropdownLink({
  to,
  children,
  onClose,
}: {
  to: string;
  children: ReactNode;
  onClose: () => void;
}) {
  return (
    <NavLink to={to} className="dropdown-item" onClick={onClose}>
      {children}
    </NavLink>
  );
}

export function Layout() {
  const { user, logout, isHr, hasRole } = useAuth();
  const location = useLocation();

  const isPayroll = hasRole("HR_PAYROLL_USER", "HR_PAYROLL_MANAGER", "ADMIN");
  const isEmployee = hasRole("EMPLOYEE");

  const onPayrollRoute = location.pathname.startsWith("/payroll");
  const onEmployeeRoute =
    location.pathname.startsWith("/employees") ||
    location.pathname.startsWith("/working-schedules") ||
    location.pathname.startsWith("/time-off/requests") ||
    location.pathname.startsWith("/time-off/types");

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">PeoplePay360</div>
        <nav className="nav">
          <NavItem to="/attendance" label="Attendance" />
          <NavItem to="/time-off/balances" label="My Balances" />
          {isHr && <NavItem to="/time-off/allocations" label="Allocations" />}

          <NavDropdown label="Employee" active={onEmployeeRoute}>
            {(close) => (
              <>
                {isHr && (
                  <DropdownLink to="/employees" onClose={close}>
                    Employees
                  </DropdownLink>
                )}
                <DropdownLink to="/time-off/requests" onClose={close}>
                  Time Off Requests
                </DropdownLink>
                <DropdownLink to="/time-off/types" onClose={close}>
                  Time Off Types
                </DropdownLink>
                {isHr && (
                  <DropdownLink to="/working-schedules" onClose={close}>
                    Working Schedules
                  </DropdownLink>
                )}
              </>
            )}
          </NavDropdown>

          {isHr && <NavItem to="/contracts" label="Contracts" />}
          {isEmployee && !isPayroll && (
            <NavItem to="/payroll/payslips" label="My Payslips" />
          )}

          {isPayroll && (
            <NavDropdown label="Payroll" active={onPayrollRoute}>
              {(close) => (
                <>
                  <DropdownLink to="/payroll/payruns" onClose={close}>
                    Payruns
                  </DropdownLink>
                  <DropdownLink to="/payroll/payslips" onClose={close}>
                    Payslips
                  </DropdownLink>
                  <DropdownLink to="/payroll/dashboard" onClose={close}>
                    Dashboard
                  </DropdownLink>
                  <span className="dropdown-sep" />
                  <span className="dropdown-label">Config</span>
                  <DropdownLink to="/payroll/structures" onClose={close}>
                    Salary Structures
                  </DropdownLink>
                  <DropdownLink to="/payroll/rules" onClose={close}>
                    Salary Rules
                  </DropdownLink>
                </>
              )}
            </NavDropdown>
          )}

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

      <main className="content">
        <Outlet />
      </main>
    </div>
  );
}