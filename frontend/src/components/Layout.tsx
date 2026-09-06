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

/** User dropdown: shows name, on click reveals email + logout */
function UserDropdown({
  name,
  email,
  roles,
  onLogout,
}: {
  name: string;
  email: string;
  roles: string[];
  onLogout: () => void;
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
        className="userbox-trigger"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
      >
        <span className="user-avatar">{name.charAt(0).toUpperCase()}</span>
        <span className="user-meta-sm">
          <span className="user-name">{name}</span>
          <span className="caret" style={{ marginLeft: 4 }}>▾</span>
        </span>
      </button>
      {open && (
        <div className="dropdown-menu user-dropdown">
          <div className="user-dropdown-header">
            <span className="user-name">{name}</span>
            <span className="user-roles">{roles.join(", ")}</span>
          </div>
          <div className="dropdown-sep" />
          <div className="user-dropdown-email">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <rect width="20" height="16" x="2" y="4" rx="2" />
              <path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7" />
            </svg>
            <span>{email}</span>
          </div>
          <div className="dropdown-sep" />
          <button className="dropdown-item" onClick={onLogout} style={{ width: "100%", textAlign: "left", border: "none", background: "none", cursor: "pointer", padding: "8px 12px", borderRadius: "7px", color: "var(--ink)", fontSize: "13px" }}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ marginRight: 8, flexShrink: 0 }}>
              <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
              <polyline points="16 17 21 12 16 7" />
              <line x1="21" x2="9" y1="12" y2="12" />
            </svg>
            Logout
          </button>
        </div>
      )}
    </div>
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
          {user && (
            <UserDropdown
              name={hasRole("ADMIN") ? "Admin" : (user.employee?.full_name ?? user.email ?? "User")}
              email={user.email ?? ""}
              roles={user.roles.map((r) => r.name)}
              onLogout={logout}
            />
          )}
        </div>
      </header>

      <main className="content">
        <Outlet />
      </main>
    </div>
  );
}