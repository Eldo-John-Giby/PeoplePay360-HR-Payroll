// Thin fetch wrapper + typed endpoint helpers for the PeoplePay360 API.
// The app talks to the Attendance & Time Off backend slice and the shared
// auth endpoints only (other teammates' APIs are wired in later).

import type {
  AllocationStatus,
  Attendance,
  AttendanceStatus,
  AttendanceSummary,
  Me,
  Page,
  RequestStatus,
  TimeOffAllocation,
  TimeOffBalance,
  TimeOffRequest,
  TimeOffType,
  TimeOffUnit,
  TokenResponse,
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
