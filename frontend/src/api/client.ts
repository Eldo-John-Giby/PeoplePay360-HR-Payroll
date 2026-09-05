// Thin fetch wrapper + typed endpoint helpers for the PeoplePay360 API.
// The app talks to the Attendance & Time Off backend slice and the shared
// auth endpoints only (other teammates' APIs are wired in later).

import type {
  AllocationStatus,
  Attendance,
  AttendanceOverview,
  AttendanceStatus,
  AttendanceSummary,
  CancelResult,
  ComputationMethod,
  ComputeResult,
  DashboardFilterOptions,
  DashboardFilters,
  DraftScopeResult,
  Kpis,
  MarkPaidResult,
  Me,
  MonthlyTrendItem,
  Page,
  PayrollAlertsResponse,
  Payrun,
  PayrunScope,
  PayrunSummary,
  Payslip,
  PayslipStatusOverview,
  PayslipSummaryItem,
  RequestStatus,
  SalaryByDepartmentItem,
  SalaryRule,
  SalaryRuleCategory,
  SalaryStructure,
  SalaryStructureSummary,
  SendPayslipsResult,
  TimeOffAllocation,
  TimeOffBalance,
  TimeOffOverview,
  TimeOffRequest,
  TimeOffType,
  TimeOffUnit,
  TokenResponse,
  UserOut,
  ValidateResult,
} from "./types";

const API_BASE = (import.meta.env.VITE_API_URL as string | undefined) ?? "/api/v1";
const TOKEN_KEY = "pp360.access_token";

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string | null): void {
  if (token) {
    localStorage.setItem(TOKEN_KEY, token);
  } else {
    localStorage.removeItem(TOKEN_KEY);
  }
}

export function logout(): void {
  setToken(null);
  // Full reload resets router/auth state cleanly.
  window.location.assign("/login");
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(
  method: string,
  path: string,
  body?: unknown,
  form?: boolean,
): Promise<T> {
  const headers: Record<string, string> = {};
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;

  let payload: BodyInit | undefined;
  if (form) {
    headers["Content-Type"] = "application/x-www-form-urlencoded";
    payload = body as URLSearchParams;
  } else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }

  const res = await fetch(`${API_BASE}${path}`, {
    method,
    headers,
    body: payload,
  });

  if (res.status === 401) {
    logout();
    throw new ApiError(401, "Session expired — please log in again.");
  }

  const text = await res.text();
  let data: unknown = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = text;
    }
  }

  if (!res.ok) {
    const detail =
      (data as { detail?: string } | null)?.detail ??
      `Request failed (${res.status})`;
    throw new ApiError(
      res.status,
      typeof detail === "string" ? detail : JSON.stringify(detail),
    );
  }
  return data as T;
}

// ---------------------------------------------------------------------------
// Auth
// ---------------------------------------------------------------------------

export function login(email: string, password: string): Promise<TokenResponse> {
  const params = new URLSearchParams({
    grant_type: "password",
    username: email,
    password,
  });
  return request<TokenResponse>("POST", "/auth/login", params, true);
}

export function fetchMe(): Promise<Me> {
  return request<Me>("GET", "/auth/me");
}

// ---------------------------------------------------------------------------
// User management (ADMIN only — the backend has no public self-signup; HR/
// admin provisions accounts, optionally linked to an existing employee).
// ---------------------------------------------------------------------------

export function fetchUsers(): Promise<UserOut[]> {
  return request<UserOut[]>("GET", "/auth/users");
}

export function createUser(payload: {
  email: string;
  password: string;
  role_names: string[];
  employee_id?: number | null;
  is_active?: boolean;
}): Promise<UserOut> {
  return request<UserOut>("POST", "/auth/users", payload);
}

export function updateUser(
  userId: number,
  payload: { employee_id?: number | null; is_active?: boolean },
): Promise<UserOut> {
  return request<UserOut>("PATCH", `/auth/users/${userId}`, payload);
}

// ---------------------------------------------------------------------------
// Attendance
// ---------------------------------------------------------------------------

export interface AttendanceFilters {
  employee_id?: number;
  status?: AttendanceStatus;
  date_from?: string;
  date_to?: string;
  is_manual_correction?: boolean;
}

function queryString(params: Record<string, string | number | boolean | undefined>): string {
  const qs = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") qs.set(key, String(value));
  }
  const s = qs.toString();
  return s ? `?${s}` : "";
}

export function listMyAttendance(pageSize = 50): Promise<Page<Attendance>> {
  return request<Page<Attendance>>(
    "GET",
    `/attendance/me${queryString({ page_size: pageSize })}`,
  );
}

export function listAttendance(
  filters: AttendanceFilters,
  pageSize = 50,
): Promise<Page<Attendance>> {
  return request<Page<Attendance>>(
    "GET",
    `/attendance${queryString({ ...filters, page_size: pageSize })}`,
  );
}

export function getAttendanceSummary(employeeId: number): Promise<AttendanceSummary> {
  return request<AttendanceSummary>("GET", `/attendance/${employeeId}/summary`);
}

export function checkIn(): Promise<Attendance> {
  return request<Attendance>("POST", "/attendance/check-in", {});
}

export function checkOut(attendanceId: number): Promise<Attendance> {
  return request<Attendance>("POST", `/attendance/${attendanceId}/check-out`);
}

export function createManualAttendance(payload: {
  employee_id: number;
  check_in: string;
  check_out: string;
  notes?: string | null;
}): Promise<Attendance> {
  return request<Attendance>("POST", "/attendance", payload);
}

/** HR correction of an existing entry (re-derives hours/status, stamps it
 * as a manual correction). All fields optional — only changed ones send. */
export function updateAttendance(
  attendanceId: number,
  payload: {
    check_in?: string;
    check_out?: string | null;
    notes?: string | null;
  },
): Promise<Attendance> {
  return request<Attendance>("PATCH", `/attendance/${attendanceId}`, payload);
}

export function sweepMissingCheckouts(): Promise<{ swept: number }> {
  return request<{ swept: number }>(
    "POST",
    "/attendance/sweep-missing-checkouts",
  );
}

// ---------------------------------------------------------------------------
// Time Off Types
// ---------------------------------------------------------------------------

export function listTimeOffTypes(): Promise<Page<TimeOffType>> {
  return request<Page<TimeOffType>>(
    "GET",
    `/time-off/types${queryString({ page_size: 100 })}`,
  );
}

export function createTimeOffType(payload: {
  name: string;
  unit: TimeOffUnit;
  requires_allocation: boolean;
  requires_approval: boolean;
  affects_payroll: boolean;
}): Promise<TimeOffType> {
  return request<TimeOffType>("POST", "/time-off/types", payload);
}

export function setTimeOffTypeActive(
  typeId: number,
  is_active: boolean,
): Promise<TimeOffType> {
  return request<TimeOffType>("PATCH", `/time-off/types/${typeId}`, {
    is_active,
  });
}

// ---------------------------------------------------------------------------
// Allocations (HR)
// ---------------------------------------------------------------------------

export function listAllocations(
  filters: { employee_id?: number; status?: AllocationStatus } = {},
): Promise<Page<TimeOffAllocation>> {
  return request<Page<TimeOffAllocation>>(
    "GET",
    `/time-off/allocations${queryString({ ...filters, page_size: 100 })}`,
  );
}

export function createAllocation(payload: {
  employee_id: number;
  time_off_type_id: number;
  allocated_amount: string;
  valid_from: string;
  valid_to?: string | null;
}): Promise<TimeOffAllocation> {
  return request<TimeOffAllocation>("POST", "/time-off/allocations", payload);
}

export function approveAllocation(id: number): Promise<TimeOffAllocation> {
  return request<TimeOffAllocation>(
    "POST",
    `/time-off/allocations/${id}/approve`,
  );
}

export function refuseAllocation(id: number): Promise<TimeOffAllocation> {
  return request<TimeOffAllocation>(
    "POST",
    `/time-off/allocations/${id}/refuse`,
  );
}

// ---------------------------------------------------------------------------
// Balances
// ---------------------------------------------------------------------------

export function listMyBalances(): Promise<TimeOffBalance[]> {
  return request<TimeOffBalance[]>("GET", "/time-off/balances/me");
}

export function listBalances(
  employeeId?: number,
): Promise<TimeOffBalance[]> {
  return request<TimeOffBalance[]>(
    "GET",
    `/time-off/balances${queryString({ employee_id: employeeId })}`,
  );
}

// ---------------------------------------------------------------------------
// Employees / org (Ameen's slice) — call his REST surface, but surface a
// clear "API not available yet" state when it 404s so the demo doesn't break.
// ---------------------------------------------------------------------------

function asEmployee(row: Record<string, unknown>): import("./types").EmployeeListItem {
  const id = Number(row.id ?? row.employee_id);
  return {
    id,
    full_name:
      typeof row.full_name === "string"
        ? row.full_name
        : typeof row.name === "string"
          ? row.name
          : `Employee #${id}`,
    work_email: (row.work_email as string | null | undefined) ?? null,
    employee_type: (row.employee_type as string | null | undefined) ?? null,
    status: (row.status as string | null | undefined) ?? "active",
    date_of_joining: (row.date_of_joining as string | null | undefined) ?? null,
    work_location: (row.work_location as string | null | undefined) ?? null,
    department_name:
      (row.department_name as string | null | undefined) ??
      (row.department as { name?: string } | null | undefined)?.name ??
      null,
    job_position_name:
      (row.job_position_name as string | null | undefined) ??
      (row.job_position as { title?: string } | null | undefined)?.title ??
      null,
    manager_name: (row.manager_name as string | null | undefined) ?? null,
    ...row,
  };
}

export interface EmployeePage {
  items: import("./types").EmployeeListItem[];
  total: number;
  page: number;
  page_size: number;
}

/** Fetch Ameen's employee directory. Returns null when his API is not up yet
 * (the Employees screen shows an explanatory empty state). */
export async function listEmployees(): Promise<EmployeePage | null> {
  try {
    const page = await request<EmployeePage>(
      "GET",
      `/employees${queryString({ page: 1, page_size: 100 })}`,
    );
    return {
      items: page.items.map((row) => asEmployee(row as Record<string, unknown>)),
      total: page.total,
      page: page.page,
      page_size: page.page_size,
    };
  } catch (err) {
    if (err instanceof ApiError && (err.status === 404 || err.status === 405)) {
      return null; // Ameen's router isn't wired yet
    }
    throw err;
  }
}

// ---------------------------------------------------------------------------
// Requests
// ---------------------------------------------------------------------------

export interface RequestFilters {
  employee_id?: number;
  status?: RequestStatus;
  time_off_type_id?: number;
}

export function listRequests(
  filters: RequestFilters = {},
  pageSize = 100,
): Promise<Page<TimeOffRequest>> {
  return request<Page<TimeOffRequest>>(
    "GET",
    `/time-off/requests${queryString({ ...filters, page_size: pageSize })}`,
  );
}

export function createRequest(payload: {
  employee_id?: number;
  time_off_type_id: number;
  date_from: string;
  date_to: string;
  duration: string;
  reason?: string | null;
}): Promise<TimeOffRequest> {
  return request<TimeOffRequest>("POST", "/time-off/requests", payload);
}

export function approveRequest(id: number): Promise<TimeOffRequest> {
  return request<TimeOffRequest>("POST", `/time-off/requests/${id}/approve`);
}

export function refuseRequest(id: number): Promise<TimeOffRequest> {
  return request<TimeOffRequest>("POST", `/time-off/requests/${id}/refuse`);
}

export function cancelRequest(id: number): Promise<TimeOffRequest> {
  return request<TimeOffRequest>("POST", `/time-off/requests/${id}/cancel`);
}

// ---------------------------------------------------------------------------
// Payroll — salary rules + structures
// ---------------------------------------------------------------------------

// --- salary rules (global library) ----------------------------------------

export interface SalaryRuleFilters {
  code?: string;
  category?: SalaryRuleCategory | "";
  is_active?: boolean;
}

export function listSalaryRules(
  filters: SalaryRuleFilters = {},
  pageSize = 100,
): Promise<Page<SalaryRule>> {
  return request<Page<SalaryRule>>(
    "GET",
    `/payroll/salary-rules${queryString({ ...filters, page_size: pageSize })}`,
  );
}

export interface SalaryRulePayload {
  code: string;
  name: string;
  category: SalaryRuleCategory;
  computation_method: ComputationMethod;
  amount?: string | null;
  percentage?: string | null;
  percentage_base_code?: string | null;
  formula?: string | null;
  default_sequence?: number;
  is_active?: boolean;
}

export function createSalaryRule(payload: SalaryRulePayload): Promise<SalaryRule> {
  return request<SalaryRule>("POST", "/payroll/salary-rules", payload);
}

export function updateSalaryRule(
  id: number,
  patch: Partial<SalaryRulePayload>,
): Promise<SalaryRule> {
  return request<SalaryRule>("PATCH", `/payroll/salary-rules/${id}`, patch);
}

export function deactivateSalaryRule(id: number): Promise<void> {
  return request<void>("DELETE", `/payroll/salary-rules/${id}`);
}

// --- salary structures (ordered rule chains) -------------------------------

export function listSalaryStructures(
  pageSize = 100,
): Promise<Page<SalaryStructureSummary>> {
  return request<Page<SalaryStructureSummary>>(
    "GET",
    `/payroll/salary-structures${queryString({ page_size: pageSize })}`,
  );
}

export function createSalaryStructure(payload: {
  name: string;
  code: string;
  is_active?: boolean;
}): Promise<SalaryStructure> {
  return request<SalaryStructure>("POST", "/payroll/salary-structures", payload);
}

export function getSalaryStructure(id: number): Promise<SalaryStructure> {
  return request<SalaryStructure>("GET", `/payroll/salary-structures/${id}`);
}

export function setStructureActive(
  id: number,
  is_active: boolean,
): Promise<SalaryStructure> {
  return request<SalaryStructure>(
    "PATCH",
    `/payroll/salary-structures/${id}`,
    { is_active },
  );
}

export function replaceStructureRules(
  structureId: number,
  rules: { salary_rule_id: number; sequence: number }[],
): Promise<SalaryStructure> {
  return request<SalaryStructure>(
    "PUT",
    `/payroll/salary-structures/${structureId}/rules`,
    { rules },
  );
}

// --- payruns: 2-step wizard + lifecycle ------------------------------------

export function draftScope(scope: PayrunScope): Promise<DraftScopeResult> {
  return request<DraftScopeResult>("POST", "/payroll/payruns/draft-scope", scope);
}

export function createPayrun(
  scope: PayrunScope,
  employeeIds: number[],
): Promise<Payrun> {
  return request<Payrun>("POST", "/payroll/payruns", {
    scope,
    employee_ids: employeeIds,
  });
}

export function listPayruns(
  filters: { status?: string; department_filter_id?: number } = {},
): Promise<Page<PayrunSummary>> {
  return request<Page<PayrunSummary>>(
    "GET",
    `/payroll/payruns${queryString({ ...filters, page_size: 100 })}`,
  );
}

export function getPayrun(id: number): Promise<Payrun> {
  return request<Payrun>("GET", `/payroll/payruns/${id}`);
}

export function computePayrun(id: number): Promise<ComputeResult> {
  return request<ComputeResult>("POST", `/payroll/payruns/${id}/compute`);
}

export function validatePayrun(id: number): Promise<ValidateResult> {
  return request<ValidateResult>("POST", `/payroll/payruns/${id}/validate`);
}

export function markPayrunPaid(id: number): Promise<MarkPaidResult> {
  return request<MarkPaidResult>("POST", `/payroll/payruns/${id}/mark-paid`);
}

export function cancelPayrun(id: number): Promise<CancelResult> {
  return request<CancelResult>("POST", `/payroll/payruns/${id}/cancel`);
}

export function sendPayrunPayslips(id: number): Promise<SendPayslipsResult> {
  return request<SendPayslipsResult>("POST", `/payroll/payruns/${id}/send-payslips`);
}

// --- payslips --------------------------------------------------------------

export function listPayslips(
  filters: { payrun_id?: number; employee_id?: number; status?: string } = {},
  pageSize = 100,
): Promise<Page<PayslipSummaryItem>> {
  return request<Page<PayslipSummaryItem>>(
    "GET",
    `/payroll/payslips${queryString({ ...filters, page_size: pageSize })}`,
  );
}

export function listMyPayslips(pageSize = 100): Promise<Page<PayslipSummaryItem>> {
  return request<Page<PayslipSummaryItem>>(
    "GET",
    `/payroll/payslips/me${queryString({ page_size: pageSize })}`,
  );
}

export function getPayslip(id: number): Promise<Payslip> {
  return request<Payslip>("GET", `/payroll/payslips/${id}`);
}

/** Download a payslip PDF (needs the bearer token; a plain <a href> won't
 * carry it, so we fetch the bytes and click a blob link). */
export async function downloadPayslipPdf(id: number): Promise<void> {
  const token = getToken();
  const res = await fetch(`${API_BASE}/payroll/payslips/${id}/pdf`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) {
    let detail = `PDF download failed (${res.status})`;
    try {
      const data = (await res.json()) as { detail?: string };
      if (data.detail) detail = data.detail;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail);
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `payslip-${id}.pdf`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

// --- dashboard (read-only analytics) ---------------------------------------
// Every endpoint composes the same optional filters (?period_start= &
// period_end= & department_id= & employee_type= & company_id=, AND logic)
// so all cards / charts / tables / alerts update together for one state.

/** Serialize DashboardFilters into a query string (empty values dropped). */
function filterQs(filters: DashboardFilters, extra?: Record<string, string | number>) {
  return queryString({
    period_start: filters.period_start,
    period_end: filters.period_end,
    department_id: filters.department_id,
    employee_type: filters.employee_type,
    company_id: filters.company_id,
    ...extra,
  });
}

export function fetchDashboardFilterOptions(): Promise<DashboardFilterOptions> {
  return request<DashboardFilterOptions>("GET", "/dashboard/filter-options");
}

export function fetchKpis(filters: DashboardFilters = {}): Promise<Kpis> {
  return request<Kpis>("GET", `/dashboard/kpis${filterQs(filters)}`);
}

export function fetchSalaryByDepartment(
  filters: DashboardFilters = {},
): Promise<SalaryByDepartmentItem[]> {
  return request<SalaryByDepartmentItem[]>(
    "GET",
    `/dashboard/salary-by-department${filterQs(filters)}`,
  );
}

export function fetchMonthlyTrend(
  months: number,
  filters: DashboardFilters = {},
): Promise<MonthlyTrendItem[]> {
  return request<MonthlyTrendItem[]>(
    "GET",
    `/dashboard/monthly-net-salary-trend${filterQs(filters, { months })}`,
  );
}

export function fetchPayrollAlerts(
  filters: DashboardFilters = {},
): Promise<PayrollAlertsResponse> {
  return request<PayrollAlertsResponse>(
    "GET",
    `/dashboard/payroll-alerts${filterQs(filters)}`,
  );
}

export function fetchPayslipStatus(
  filters: DashboardFilters = {},
): Promise<PayslipStatusOverview> {
  return request<PayslipStatusOverview>(
    "GET",
    `/dashboard/payslip-status${filterQs(filters)}`,
  );
}

export function fetchAttendanceOverview(
  filters: DashboardFilters = {},
): Promise<AttendanceOverview> {
  return request<AttendanceOverview>(
    "GET",
    `/dashboard/attendance-overview${filterQs(filters)}`,
  );
}

export function fetchTimeOffOverview(
  filters: DashboardFilters = {},
): Promise<TimeOffOverview> {
  return request<TimeOffOverview>(
    "GET",
    `/dashboard/time-off-overview${filterQs(filters)}`,
  );
}
