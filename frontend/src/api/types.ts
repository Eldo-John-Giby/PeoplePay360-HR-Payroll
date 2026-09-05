// Response types mirroring the PeoplePay360 API (attendance + time off +
// auth/me). Kept aligned with the Pydantic schemas on the backend.

export interface TokenResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
}

export interface RoleRef {
  id: number;
  name: string;
  description?: string | null;
}

export interface EmployeeRef {
  id: number;
  full_name: string;
  work_email: string;
}

export interface Me {
  id: number;
  email: string;
  employee_id: number | null;
  is_active: boolean;
  roles: RoleRef[];
  employee: EmployeeRef | null;
}

export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export type AttendanceStatus =
  | "present"
  | "late"
  | "absent"
  | "overtime"
  | "missing_checkout";

export interface Attendance {
  id: number;
  employee_id: number;
  employee_name: string | null;
  check_in: string; // ISO timestamptz
  check_out: string | null;
  worked_hours: string | null; // Decimal serialized as string
  status: AttendanceStatus;
  is_manual_correction: boolean;
  corrected_by_user_id: number | null;
  notes: string | null;
  created_at: string;
  updated_at: string;
}

export interface AttendanceSummary {
  employee_id: number;
  employee_name: string | null;
  date_from: string;
  date_to: string;
  expected_workdays: number;
  present: number;
  late: number;
  overtime: number;
  missing_checkout: number;
  absent: number;
  coverage_pct: number;
}

export type TimeOffUnit = "days" | "hours";

export interface TimeOffType {
  id: number;
  name: string;
  unit: TimeOffUnit;
  requires_allocation: boolean;
  requires_approval: boolean;
  affects_payroll: boolean;
  company_id: number | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export type AllocationStatus = "draft" | "to_approve" | "approved" | "refused";

export interface TimeOffAllocation {
  id: number;
  employee_id: number;
  employee_name: string | null;
  time_off_type_id: number;
  type_name: string | null;
  allocated_amount: string;
  valid_from: string;
  valid_to: string | null;
  status: AllocationStatus;
  approver_id: number | null;
  version_id: number;
  created_at: string;
  updated_at: string;
}

export interface TimeOffBalance {
  employee_id: number;
  employee_name: string | null;
  time_off_type_id: number;
  type_name: string;
  unit: TimeOffUnit;
  allocated: string;
  taken: string;
  remaining: string;
}

export type RequestStatus =
  | "draft"
  | "to_approve"
  | "approved"
  | "refused"
  | "cancelled";

export interface TimeOffRequest {
  id: number;
  employee_id: number;
  employee_name: string | null;
  time_off_type_id: number;
  type_name: string | null;
  unit: TimeOffUnit | null;
  date_from: string;
  date_to: string;
  duration: string;
  status: RequestStatus;
  approver_id: number | null;
  reason: string | null;
  warnings: string[];
  created_at: string;
  updated_at: string;
}

export interface ApiError {
  detail: string;
  error_code?: string;
}

// ---------------------------------------------------------------------------
// Employees / org (Ameen's slice)
// ---------------------------------------------------------------------------
// The frontend screens wrap Ameen's endpoints. Shapes follow the shared
// Employee / Department models; fields are read leniently so the UI degrades
// gracefully until his API contract is merged.

export type EmployeeStatus = "active" | "inactive" | "terminated";

export interface EmployeeListItem {
  id: number;
  full_name?: string;
  name?: string;
  work_email?: string | null;
  employee_type?: string | null;
  status?: EmployeeStatus | string | null;
  date_of_joining?: string | null;
  work_location?: string | null;
  department_name?: string | null;
  job_position_name?: string | null;
  manager_name?: string | null;
  [key: string]: unknown;
}
