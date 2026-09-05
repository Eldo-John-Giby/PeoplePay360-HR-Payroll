import { Navigate, Route, Routes } from "react-router-dom";
import type { ReactNode } from "react";

import { useAuth } from "./auth";
import { Layout } from "./components/Layout";
import { LandingPage } from "./pages/LandingPage";
import { LoginPage } from "./pages/LoginPage";
import { AttendancePage } from "./pages/AttendancePage";
import { TimeOffRequestsPage } from "./pages/TimeOffRequestsPage";
import { BalancesPage } from "./pages/BalancesPage";
import { TypesPage } from "./pages/TypesPage";
import { AllocationsPage } from "./pages/AllocationsPage";
import { EmployeesPage } from "./pages/EmployeesPage";
import { AccountsPage } from "./pages/AccountsPage";
import { ContractsPage } from "./pages/ContractsPage";
import { WorkingSchedulesPage } from "./pages/WorkingSchedulesPage";
import { SalaryStructuresPage } from "./pages/SalaryStructuresPage";
import { SalaryRulesPage } from "./pages/SalaryRulesPage";
import { SalaryRuleDetailPage } from "./pages/SalaryRuleDetailPage";
import { PayrunsPage } from "./pages/PayrunsPage";
import { PayrunProcessingPage } from "./pages/PayrunProcessingPage";
import { PayslipsPage } from "./pages/PayslipsPage";
import { PayslipDetailPage } from "./pages/PayslipDetailPage";
import { PayrollDashboardPage } from "./pages/PayrollDashboardPage";
import { AdminPage } from "./pages/AdminPage";

function RequireAuth({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth();
  if (loading) return <div className="center">Loading…</div>;
  // Not signed in → back to the landing page, from where they can sign in.
  if (!user) return <Navigate to="/" replace />;
  return <>{children}</>;
}

function RequireAdmin({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  if (!user?.roles.some((r) => r.name === "ADMIN")) {
    return <Navigate to="/" replace />;
  }
  return <>{children}</>;
}

export default function App() {
  return (
    <Routes>
      {/* Public marketing landing page - the entry point of the app. */}
      <Route path="/" element={<LandingPage />} />
      <Route path="/login" element={<LoginPage />} />

      {/* Authenticated console. */}
      <Route
        element={
          <RequireAuth>
            <Layout />
          </RequireAuth>
        }
      >
        <Route path="/attendance" element={<AttendancePage />} />
        <Route path="/employees" element={<EmployeesPage />} />
        <Route path="/contracts" element={<ContractsPage />} />
        <Route path="/working-schedules" element={<WorkingSchedulesPage />} />
        <Route path="/time-off/requests" element={<TimeOffRequestsPage />} />
        <Route path="/time-off/balances" element={<BalancesPage />} />
        <Route path="/time-off/types" element={<TypesPage />} />
        <Route path="/time-off/allocations" element={<AllocationsPage />} />
        <Route path="/payroll/payruns" element={<PayrunsPage />} />
        <Route path="/payroll/payruns/:id" element={<PayrunProcessingPage />} />
        <Route path="/payroll/payslips" element={<PayslipsPage />} />
        <Route path="/payroll/payslips/:id" element={<PayslipDetailPage />} />
        <Route path="/payroll/dashboard" element={<PayrollDashboardPage />} />
        <Route path="/payroll/structures" element={<SalaryStructuresPage />} />
        <Route path="/payroll/rules" element={<SalaryRulesPage />} />
        <Route path="/payroll/rules/:id" element={<SalaryRuleDetailPage />} />
        <Route
          path="/accounts"
          element={
            <RequireAdmin>
              <AccountsPage />
            </RequireAdmin>
          }
        />
        <Route
          path="/admin"
          element={
            <RequireAdmin>
              <AdminPage />
            </RequireAdmin>
          }
        />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
