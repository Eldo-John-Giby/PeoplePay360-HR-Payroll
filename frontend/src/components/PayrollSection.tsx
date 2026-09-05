// Payroll section wrapper — sub-navigation + role gate for everything under
// /payroll. Backend RBAC split:
//   HR_PAYROLL_USER / HR_PAYROLL_MANAGER / ADMIN -> the full payroll section
//   EMPLOYEE -> self-service only (their own payslips + PDF download)
//   HR_MANAGER -> no payroll access at all (not even the section)
// The section itself renders a tab bar and the nested page via <Outlet />.

import { Navigate, NavLink, Outlet } from "react-router-dom";

import { useAuth } from "../auth";

function Tab({ to, label }: { to: string; label: string }) {
  return (
    <NavLink
      to={to}
      className={({ isActive }) => (isActive ? "tab active" : "tab")}
    >
      {label}
    </NavLink>
  );
}

export function PayrollSection() {
  const { hasRole } = useAuth();
  const isPayroll = hasRole("HR_PAYROLL_USER", "HR_PAYROLL_MANAGER", "ADMIN");
  const isEmployee = hasRole("EMPLOYEE");

  if (!isPayroll && !isEmployee) return <Navigate to="/" replace />;

  return (
    <div>
      <div className="tabbar">
        {isPayroll && <Tab to="/payroll/overview" label="Overview" />}
        {isPayroll && <Tab to="/payroll/payruns" label="Payruns" />}
        <Tab
          to="/payroll/payslips"
          label={isPayroll ? "Payslips" : "My payslips"}
        />
        {isPayroll && <Tab to="/payroll/salary-rules" label="Salary rules" />}
        {isPayroll && (
          <Tab to="/payroll/salary-structures" label="Structures" />
        )}
      </div>
      <div style={{ marginTop: 18 }}>
        <Outlet />
      </div>
    </div>
  );
}
