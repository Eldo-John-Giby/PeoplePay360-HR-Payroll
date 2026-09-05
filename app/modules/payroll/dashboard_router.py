"""Payroll Dashboard / analytics endpoints (Steve's slice).

CONNECTIONS MAP (read this first):
- MOUNTED BY: app/main.py (FROZEN, Eldo's) -> prefix="/api/v1/dashboard",
  so every route below is /api/v1/dashboard/... (dashboard and payroll use
  DIFFERENT prefixes even though they live in one module).- WHAT I DO: thin HTTP layer for the eight read-only analytics endpoints.
    Each one: RBAC gate -> pass the shared filter query params (period /
    department / employee type / company — AND-composed in service.py) to
    ONE service function -> return the aggregate DTO. NO business logic here.
- CALLS: service.py functions get_kpis / get_salary_by_department /
  get_monthly_net_salary_trend / get_attendance_overview /
  get_time_off_overview / get_payroll_alerts. The service layer owns the
  shared filter-building helpers (_employee_scope_filter / _period_overlap)
  so every endpoint composes department + employee_type + period identically.
- DATA SOURCES (all read-only): payslips/payruns (mine), employees/contracts
  (Ameen), attendances (Ambuj), time_off_* + v_time_off_balances view (Ambuj/
  Eldo). This is the module that aggregates ACROSS every other team member's
  tables — the reason the dashboard is "Steve's analytics" slice.

Eight read-only aggregation endpoints across every module's tables. All of
them compose the same AND filters (?period_start=&period_end=&department_id=&
employee_type=&company_id=) via the shared helpers in service.py
(_employee_scope_filter / _filtered_employee_ids). /filter-options feeds the
UI's filter bar with live companies/departments/employee types.

RBAC: payroll data is HR_PAYROLL_USER+ only — EMPLOYEE and HR_MANAGER get 403
on every dashboard endpoint (arch doc §4.7).
"""

from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.dependencies import require_roles
from app.models.auth import User
from app.models.enums import EmployeeType
from app.schemas.payroll import (
    AttendanceOverview,
    DashboardFilterOptionsResponse,
    KpisResponse,
    MonthlyTrendItem,
    PayrollAlertsResponse,
    PayslipStatusOverview,
    SalaryByDepartmentItem,
    TimeOffOverview,
)

from . import service

router = APIRouter()

# Every dashboard endpoint shows payroll-derived analytics, so ALL of them
# require a payroll role. EMPLOYEE and HR_MANAGER are deliberately excluded
# (arch §4.7: HR_MANAGER has no payroll access; EMPLOYEE sees only their own
# payslips, never company-wide aggregates).
DASHBOARD_ROLES = require_roles("HR_PAYROLL_USER", "HR_PAYROLL_MANAGER", "ADMIN")


@router.get("/filter-options", response_model=DashboardFilterOptionsResponse)
def filter_options(
    _: User = Depends(DASHBOARD_ROLES),
    db: Session = Depends(get_db),
):
    """Live option lists (companies / departments / employee types) for the
    dashboard filter bar — nothing hardcoded on the client."""
    return service.get_dashboard_filter_options(db)


@router.get("/kpis", response_model=KpisResponse)
def kpis(
    _: User = Depends(DASHBOARD_ROLES),
    db: Session = Depends(get_db),
    period_start: date | None = None,
    period_end: date | None = None,
    department_id: int | None = None,
    employee_type: EmployeeType | None = None,
    company_id: int | None = None,
):
    """{total_net_salary_paid (paid only), payslips_generated, average_salary,
    approved_time_off_days, attendance_health_pct}."""
    # The headline KPI cards. CRITICAL semantics (kept in the service):
    # total_net_salary_paid sums payslips with status='paid' ONLY — the
    # spec calls this out as the demo-breaking bug to avoid. average_salary
    # spans computed+validated+paid (a draft isn't a real amount yet).
    return service.get_kpis(
        db, period_start, period_end, department_id, employee_type, company_id
    )


@router.get("/salary-by-department", response_model=list[SalaryByDepartmentItem])
def salary_by_department(
    _: User = Depends(DASHBOARD_ROLES),
    db: Session = Depends(get_db),
    period_start: date | None = None,
    period_end: date | None = None,
    department_id: int | None = None,
    employee_type: EmployeeType | None = None,
    company_id: int | None = None,
):
    """Bar chart: [{department_name, total_salary (paid net), headcount}]."""
    # headcount = ACTIVE employees per department (from Ameen's employees
    # table); total_salary = sum of PAID net payslips. Two grouped queries
    # merged in the service. Note the return is a bare list (not Page).
    return service.get_salary_by_department(
        db, period_start, period_end, department_id, employee_type, company_id
    )


@router.get("/monthly-net-salary-trend", response_model=list[MonthlyTrendItem])
def monthly_net_salary_trend(
    _: User = Depends(DASHBOARD_ROLES),
    db: Session = Depends(get_db),
    months: int = Query(6, ge=1, le=24),
    period_start: date | None = None,
    period_end: date | None = None,
    department_id: int | None = None,
    employee_type: EmployeeType | None = None,
    company_id: int | None = None,
):
    """Line chart: last N months of PAID payruns (missing months -> 0)."""
    # months defaults to 6 (clamped 1-24). Uses date_trunc('month', ...) on
    # period_end and fills months with no paid data as 0 so the line chart
    # never has gaps (see service._add_months for calendar-accurate math).
    return service.get_monthly_net_salary_trend(
        db, months, period_start, period_end, department_id, employee_type, company_id
    )


@router.get("/attendance-overview", response_model=AttendanceOverview)
def attendance_overview(
    _: User = Depends(DASHBOARD_ROLES),
    db: Session = Depends(get_db),
    period_start: date | None = None,
    period_end: date | None = None,
    department_id: int | None = None,
    employee_type: EmployeeType | None = None,
    company_id: int | None = None,
):
    """{present, late, absent (computed), overtime, missing_checkouts,
    manual_edits, coverage_pct}."""
    # Reads Ambuj's attendances table. IMPORTANT: Ambuj's schema stores no
    # synthetic 'absent' rows — absence is COMPUTED here by diffing each
    # employee's schedule-expected days vs actual attendance (service logic).
    # When no period filter is passed the service defaults to the current
    # calendar month so the endpoint is useful out of the box.
    return service.get_attendance_overview(
        db, period_start, period_end, department_id, employee_type, company_id
    )


@router.get("/time-off-overview", response_model=TimeOffOverview)
def time_off_overview(
    _: User = Depends(DASHBOARD_ROLES),
    db: Session = Depends(get_db),
    period_start: date | None = None,
    period_end: date | None = None,
    department_id: int | None = None,
    employee_type: EmployeeType | None = None,
    company_id: int | None = None,
):
    """{approved_days, pending_requests, balances_by_type} — balances come
    from the live v_time_off_balances view."""
    # approved_days sums APPROVED day-unit requests overlapping the period;
    # pending counts to_approve rows; balances_by_type aggregates the LIVE
    # SQL view v_time_off_balances (allocated - taken) — Eldo deliberately
    # keeps leave balances as a view, not a stored column (README §5).
    return service.get_time_off_overview(
        db, period_start, period_end, department_id, employee_type, company_id
    )


@router.get("/payroll-alerts", response_model=PayrollAlertsResponse)
def payroll_alerts(
    _: User = Depends(DASHBOARD_ROLES),
    db: Session = Depends(get_db),
    period_start: date | None = None,
    period_end: date | None = None,
    department_id: int | None = None,
    employee_type: EmployeeType | None = None,
    company_id: int | None = None,
):
    """Open warnings across draft/computed payslips, grouped by type with
    counts + drill-down payslip ids."""
    # The "action needed" list (e.g. missing bank details, missing contract,
    # overlapping periods) for HR. Only DRAFT/COMPUTED payslips count as
    # "open" (validated/paid are resolved/historical). Internal SENT_AT
    # sentinel rows are excluded so they never surface as alerts.
    return service.get_payroll_alerts(
        db, period_start, period_end, department_id, employee_type, company_id
    )


@router.get("/payslip-status", response_model=PayslipStatusOverview)
def payslip_status(
    _: User = Depends(DASHBOARD_ROLES),
    db: Session = Depends(get_db),
    period_start: date | None = None,
    period_end: date | None = None,
    department_id: int | None = None,
    employee_type: EmployeeType | None = None,
    company_id: int | None = None,
):
    """Payslip status distribution (paid/validated/computed/draft) + derived
    unvalidated & with-warnings counts for the 'Payslip Status' panel."""
    return service.get_payslip_status_overview(
        db, period_start, period_end, department_id, employee_type, company_id
    )