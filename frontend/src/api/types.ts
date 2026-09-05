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

export interface UserOut {
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

// ---------------------------------------------------------------------------
// Payroll (Steve's slice — salary config, payruns, payslips, dashboard)
// ---------------------------------------------------------------------------
// Shapes mirror the Pydantic schemas in app/schemas/payroll.py. Money arrives
// as strings (Decimal serialization); parse with Number() for display only.

export type ComputationMethod = "fixed" | "percentage" | "formula";

export type SalaryRuleCategory =
  | "basic"
  | "allowance"
  | "deduction"
  | "gross"
  | "contribution"
  | "net";

export type PayrunStatus = "draft" | "computed" | "validated" | "paid" | "cancelled";

export type PayslipWarningType =
  | "missing_bank_details"
  | "duplicate_payslip"
  | "missing_contract"
  | "negative_net"
  | "overlapping_period"
  | "other";

export interface SalaryRule {
  id: number;
  code: string;
  name: string;
  category: SalaryRuleCategory;
  computation_method: ComputationMethod;
  amount: string | null;
  percentage: string | null;
  percentage_base_code: string | null;
  formula: string | null;
  default_sequence: number;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface SalaryStructureSummary {
  id: number;
  name: string;
  code: string;
  is_active: boolean;
  rule_count: number;
}

export interface SalaryStructureRuleItem {
  sequence: number;
  rule: SalaryRule;
}

export interface SalaryStructure {
  id: number;
  name: string;
  code: string;
  company_id: number | null;
  is_active: boolean;
  rules: SalaryStructureRuleItem[];
  created_at: string;
  updated_at: string;
}

export interface PayrunScope {
  salary_structure_id: number;
  period_start: string;
  period_end: string;
  department_filter_id?: number | null;
  employee_type_filter?: string | null;
  name?: string | null;
}

export interface EligibleEmployee {
  id: number;
  full_name: string;
  work_email: string;
  department_name: string;
  employee_type: string;
  status: string;
  has_contract: boolean;
}

export interface DraftScopeResult {
  scope: PayrunScope;
  eligible_employees: EligibleEmployee[];
  eligible_count: number;
}

export interface PayrunSummary {
  id: number;
  name: string;
  salary_structure_id: number;
  period_start: string;
  period_end: string;
  department_filter_id: number | null;
  employee_type_filter: string | null;
  status: PayrunStatus;
  created_by_user_id: number;
  payslip_count: number;
  employee_count: number;
  created_at: string;
}

export interface PayrunPayslipSummary {
  id: number;
  employee_id: number;
  employee_name: string;
  net_salary: string;
  status: PayrunStatus;
  warning_count: number;
}

export interface Payrun {
  id: number;
  name: string;
  salary_structure_id: number;
  period_start: string;
  period_end: string;
  department_filter_id: number | null;
  employee_type_filter: string | null;
  status: PayrunStatus;
  created_by_user_id: number;
  version_id: number;
  created_at: string;
  updated_at: string;
  payslips: PayrunPayslipSummary[];
}

export interface ComputeSkippedItem {
  payslip_id: number;
  employee_name: string;
  reason: string;
}

export interface ComputeResult {
  payrun_id: number;
  status: PayrunStatus;
  payslips_computed: number;
  payslips_skipped: ComputeSkippedItem[];
  warnings_added: number;
}

export interface ValidateResult {
  payrun_id: number;
  status: PayrunStatus;
  validated_payslips: number;
  blocking_warnings: string[];
}

export interface MarkPaidResult {
  payrun_id: number;
  status: PayrunStatus;
  paid_payslips: number;
}

export interface CancelResult {
  payrun_id: number;
  status: PayrunStatus;
  cancelled_payslips: number;
}

export interface SendPayslipResultItem {
  employee_id: number;
  employee_name: string;
  sent: boolean;
  error: string | null;
}

export interface SendPayslipsResult {
  payrun_id: number;
  sent_count: number;
  skipped_count: number;
  results: SendPayslipResultItem[];
}

export interface PayslipLine {
  id: number;
  salary_rule_id: number;
  sequence: number;
  code: string;
  name: string;
  category: SalaryRuleCategory;
  amount: string;
}

export interface PayslipWarning {
  id: number;
  warning_type: PayslipWarningType;
  message: string;
  created_at: string;
}

export interface PayslipSummaryItem {
  id: number;
  payrun_id: number;
  employee_id: number;
  employee_name: string;
  period_start: string;
  period_end: string;
  gross_salary: string;
  net_salary: string;
  status: PayrunStatus;
  warning_count: number;
}

export interface Payslip {
  id: number;
  payrun_id: number;
  employee_id: number;
  employee_name: string;
  contract_id: number | null;
  period_start: string;
  period_end: string;
  worked_days: string;
  gross_salary: string;
  net_salary: string;
  status: PayrunStatus;
  version_id: number;
  created_at: string;
  updated_at: string;
  lines: PayslipLine[];
  warnings: PayslipWarning[];
}

export interface Kpis {
  total_net_salary_paid: string;
  payslips_generated: number;
  average_salary: string;
  approved_time_off_days: string;
  attendance_health_pct: number;
}

/** Composable dashboard filters — all optional, AND-composed server-side. */
export interface DashboardFilters {
  period_start?: string;
  period_end?: string;
  department_id?: number;
  employee_type?: string;
  company_id?: number;
}

export interface FilterOption {
  id: number;
  name: string;
  company_id: number | null;
}

export interface DashboardFilterOptions {
  companies: FilterOption[];
  departments: FilterOption[];
  employee_types: string[];
}

export interface PayslipStatusOverview {
  draft: number;
  computed: number;
  validated: number;
  paid: number;
  cancelled: number;
  unvalidated: number; // draft + computed — amounts not yet signed off
  with_warnings: number;
}

export interface SalaryByDepartmentItem {
  department_name: string;
  total_salary: string;
  headcount: number;
}

export interface MonthlyTrendItem {
  month: string;
  total_net_salary: string;
}

export interface PayrollAlertItem {
  warning_type: PayslipWarningType;
  count: number;
  payslip_ids: number[];
}

export interface PayrollAlertsResponse {
  alerts: PayrollAlertItem[];
  total_open_payslips: number;
  unvalidated_payslips: number;
}

export interface AttendanceOverview {
  present: number;
  late: number;
  absent: number;
  overtime: number;
  missing_checkouts: number;
  manual_edits: number;
  coverage_pct: number;
}

export interface TimeOffTypeOverviewItem {
  time_off_type_name: string;
  approved_days: string;
  pending_requests: number;
  remaining: string;
}

export interface TimeOffOverview {
  approved_days: string;
  pending_requests: number;
  balances_by_type: { time_off_type_name: string; remaining: string }[];
  by_type: TimeOffTypeOverviewItem[];
}
