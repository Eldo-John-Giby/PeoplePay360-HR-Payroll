import { Navigate, Route, Routes } from "react-router-dom";
import type { ReactNode } from "react";

import { useAuth } from "./auth";
import { Layout } from "./components/Layout";
import { LoginPage } from "./pages/LoginPage";
import { AttendancePage } from "./pages/AttendancePage";
import { TimeOffRequestsPage } from "./pages/TimeOffRequestsPage";
import { BalancesPage } from "./pages/BalancesPage";
import { TypesPage } from "./pages/TypesPage";
import { AllocationsPage } from "./pages/AllocationsPage";
import { EmployeesPage } from "./pages/EmployeesPage";
import { AccountsPage } from "./pages/AccountsPage";

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
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
