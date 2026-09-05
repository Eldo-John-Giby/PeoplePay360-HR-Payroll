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
// Organization - departments / job positions (Ameen's slice)
// ---------------------------------------------------------------------------

export interface DepartmentSummary {
  id: number;
  name: string;
  is_active?: boolean;
}

export interface JobPositionSummary {
  id: number;
  title: string;
  is_active?: boolean;
}

export interface EmployeeSummaryRef {
  id: number;
  full_name: string;
  work_email: string;
  status?: string | null;
}

// ---------------------------------------------------------------------------
// Contracts (Ameen's slice)
// ---------------------------------------------------------------------------

export type ContractStatus = "draft" | "running" | "expired" | "cancelled";

export interface WorkingScheduleRef {
  id: number;
  name: string;
  schedule_type?: string | null;
  total_weekly_hours?: string | null;
}

export interface SalaryStructureRef {
  id: number;
  name: string;
  code: string;
}

export interface Contract {
  id: number;
  contract_number: string;
  employee: EmployeeSummaryRef | null;
  department: DepartmentSummary | null;
  job_position: JobPositionSummary | null;
  working_schedule: WorkingScheduleRef | null;
  salary_structure: SalaryStructureRef | null;
  wage_monthly: string;
  start_date: string;
  end_date: string | null;
  status: ContractStatus;
  version_id: number;
  created_at: string;
  updated_at: string;
}

export interface ContractCreatePayload {
  employee_id: number;
  department_id: number;
  job_position_id: number;
  working_schedule_id: number;
  salary_structure_id: number;
  wage_monthly: string;
  start_date: string;
  end_date?: string | null;
}

export interface ContractUpdatePayload {
  version_id?: number | null;
  department_id?: number | null;
  job_position_id?: number | null;
  working_schedule_id?: number | null;
  salary_structure_id?: number | null;
  wage_monthly?: string | null;
  start_date?: string | null;
  end_date?: string | null;
}

// ---------------------------------------------------------------------------
// Working schedules (Ameen's slice)
// ---------------------------------------------------------------------------

export type ScheduleType = "full_time" | "part_time" | "custom";

export interface WorkingScheduleLine {
  id: number;
  working_schedule_id: number;
  day_of_week: number; // 0=Mon .. 6=Sun
  start_time: string; // "HH:MM:SS"
  end_time: string;
  break_minutes: number;
}

export interface WorkingScheduleLineInput {
  day_of_week: number;
  start_time: string;
  end_time: string;
  break_minutes: number;
}

export interface WorkingScheduleItem {
  id: number;
  name: string;
  schedule_type: ScheduleType;
  is_active: boolean;
  total_weekly_hours: string;
  created_at?: string;
  updated_at?: string;
}

export interface WorkingScheduleDetail extends WorkingScheduleItem {
  lines: WorkingScheduleLine[];
}

export interface WorkingScheduleCreatePayload {
  name: string;
  schedule_type: ScheduleType;
  is_active?: boolean;
  lines: WorkingScheduleLineInput[];
}

// ---------------------------------------------------------------------------
// Payroll - salary rules & structures (Steve's slice)
// ---------------------------------------------------------------------------

export type SalaryRuleCategory =
  | "basic"
  | "allowance"
  | "deduction"
  | "gross"
  | "contribution"
  | "net";

export type ComputationMethod = "fixed" | "percentage" | "formula";

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

export interface SalaryRuleCreatePayload {
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

export interface SalaryRuleUpdatePayload {
  code?: string;
  name?: string;
  category?: SalaryRuleCategory;
  computation_method?: ComputationMethod;
  amount?: string | null;
  percentage?: string | null;
  percentage_base_code?: string | null;
  formula?: string | null;
  default_sequence?: number;
  is_active?: boolean;
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
  is_active: boolean;
  rules: SalaryStructureRuleItem[];
  created_at: string;
  updated_at: string;
}

export interface SalaryStructureCreatePayload {
  name: string;
  code: string;
  is_active?: boolean;
}

// ---------------------------------------------------------------------------
// Payroll - payruns
// ---------------------------------------------------------------------------

export type PayrunStatus = "draft" | "computed" | "validated" | "paid" | "cancelled";

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

export interface DraftScopeResponse {
  scope: PayrunScope;
  eligible_employees: EligibleEmployee[];
  eligible_count: number;
}

export interface PayrunPayslipSummary {
  id: number;
  employee_id: number;
  employee_name: string;
  net_salary: string;
  status: PayrunStatus;
  warning_count: number;
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
  payslip_count: number;
  employee_count: number;
  created_at: string;
}

export interface PayrunDetail {
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

export interface ComputeResult {
  payrun_id: number;
  status: PayrunStatus;
  payslips_computed: number;
  payslips_skipped: { payslip_id: number; employee_name: string; reason: string }[];
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

export interface SendPayslipsResult {
  payrun_id: number;
  sent_count: number;
  skipped_count: number;
  results: { employee_id: number; employee_name: string; sent: boolean; error: string | null }[];
}

// ---------------------------------------------------------------------------
// Payroll - payslips
// ---------------------------------------------------------------------------

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
  warning_type: string;
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

export interface PayslipDetail {
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

// ---------------------------------------------------------------------------
// Dashboard (Steve's slice)
// ---------------------------------------------------------------------------

export interface DashboardKpis {
  total_net_salary_paid: string;
  payslips_generated: number;
  average_salary: string;
  approved_time_off_days: string;
  attendance_health_pct: number;
}

export interface SalaryByDepartmentItem {
  department_name: string;
  total_salary: string;
  headcount: number;
}

export interface MonthlyTrendItem {
  month: string; // "YYYY-MM"
  total_net_salary: string;
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

export interface TimeOffBalanceItem {
  time_off_type_name: string;
  remaining: string;
}

export interface TimeOffOverview {
  approved_days: string;
  pending_requests: number;
  by_type: TimeOffBalanceItem[];
}

export interface PayrollAlertItem {
  warning_type: string;
  count: number;
  payslip_ids: number[];
}

export interface PayrollAlerts {
  alerts: PayrollAlertItem[];
  total_open_payslips: number;
  unvalidated_payslips: number;
}

export interface PayslipStatusOverview {
  paid: number;
  validated: number;
  computed: number;
  draft: number;
  with_warnings: number;
  unvalidated: number;
}
