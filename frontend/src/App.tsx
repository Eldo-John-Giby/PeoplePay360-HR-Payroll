import { Navigate, Route, Routes } from "react-router-dom";
import type { ReactNode } from "react";

import { useAuth } from "./auth";
import { Layout } from "./components/Layout";
import { PayrollSection } from "./components/PayrollSection";
import { LoginPage } from "./pages/LoginPage";
import { AttendancePage } from "./pages/AttendancePage";
import { TimeOffRequestsPage } from "./pages/TimeOffRequestsPage";
import { BalancesPage } from "./pages/BalancesPage";
import { TypesPage } from "./pages/TypesPage";
import { AllocationsPage } from "./pages/AllocationsPage";
import { EmployeesPage } from "./pages/EmployeesPage";
import { AccountsPage } from "./pages/AccountsPage";
import { PayrollDashboardPage } from "./pages/PayrollDashboardPage";
import { PayrunsPage } from "./pages/PayrunsPage";
import { PayrunDetailPage } from "./pages/PayrunDetailPage";
import { PayslipsPage } from "./pages/PayslipsPage";
import { PayslipDetailPage } from "./pages/PayslipDetailPage";
import { SalaryRulesPage } from "./pages/SalaryRulesPage";
import { SalaryStructuresPage } from "./pages/SalaryStructuresPage";

function RequireAuth({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth();
  if (loading) return <div className="center">Loading…</div>;
  if (!user) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

function RequireAdmin({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  if (!user?.roles.some((r) => r.name === "ADMIN")) {
    return <Navigate to="/" replace />;
  }
  return <>{children}</>;
}

/** Payroll read role (HR_PAYROLL_USER / HR_PAYROLL_MANAGER / ADMIN). The
 * self-service payslip pages are deliberately NOT behind this — EMPLOYEE
 * users reach those through the section. */
function RequirePayrollRead({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  const ok = user?.roles.some((r) =>
    ["HR_PAYROLL_USER", "HR_PAYROLL_MANAGER", "ADMIN"].includes(r.name),
  );
  if (!ok) return <Navigate to="/payroll/payslips" replace />;
  return <>{children}</>;
}

function PayrollIndexRedirect() {
  const { user } = useAuth();
  const isPayroll = Boolean(
    user?.roles.some((r) =>
      ["HR_PAYROLL_USER", "HR_PAYROLL_MANAGER", "ADMIN"].includes(r.name),
    ),
  );
  return <Navigate to={isPayroll ? "/payroll/overview" : "/payroll/payslips"} replace />;
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route
        element={
          <RequireAuth>
            <Layout />
          </RequireAuth>
        }
      >
        <Route index element={<Navigate to="/attendance" replace />} />
        <Route path="/employees" element={<EmployeesPage />} />
        <Route path="/attendance" element={<AttendancePage />} />
        <Route path="/time-off/requests" element={<TimeOffRequestsPage />} />
        <Route path="/time-off/balances" element={<BalancesPage />} />
        <Route path="/time-off/types" element={<TypesPage />} />
        <Route path="/time-off/allocations" element={<AllocationsPage />} />
        <Route
          path="/accounts"
          element={
            <RequireAdmin>
              <AccountsPage />
            </RequireAdmin>
          }
        />

        {/* Payroll section — role-aware tab bar inside */}
        <Route path="/payroll" element={<PayrollSection />}>
          <Route index element={<PayrollIndexRedirect />} />
          <Route
            path="overview"
            element={
              <RequirePayrollRead>
                <PayrollDashboardPage />
              </RequirePayrollRead>
            }
          />
          <Route
            path="payruns"
            element={
              <RequirePayrollRead>
                <PayrunsPage />
              </RequirePayrollRead>
            }
          />
          <Route
            path="payruns/:payrunId"
            element={
              <RequirePayrollRead>
                <PayrunDetailPage />
              </RequirePayrollRead>
            }
          />
          {/* payslips: payroll roles see the full register, EMPLOYEE only /me */}
          <Route path="payslips" element={<PayslipsPage />} />
          <Route path="payslips/:payslipId" element={<PayslipDetailPage />} />
          <Route
            path="salary-rules"
            element={
              <RequirePayrollRead>
                <SalaryRulesPage />
              </RequirePayrollRead>
            }
          />
          <Route
            path="salary-structures"
            element={
              <RequirePayrollRead>
                <SalaryStructuresPage />
              </RequirePayrollRead>
            }
          />
        </Route>
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
