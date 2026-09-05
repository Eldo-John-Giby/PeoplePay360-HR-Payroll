"""Payroll Dashboard / analytics endpoints (Steve's slice).

Read-only aggregation endpoints across every module's tables. All endpoints
share the same filter set (?period_start=&period_end=&department_id=&
employee_type=) via one shared filter-building helper in service.py.

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
    KpisResponse,
    MonthlyTrendItem,
    PayrollAlertsResponse,
    SalaryByDepartmentItem,
    TimeOffOverview,
)

from . import service

router = APIRouter()

DASHBOARD_ROLES = require_roles("HR_PAYROLL_USER", "HR_PAYROLL_MANAGER", "ADMIN")


@router.get("/kpis", response_model=KpisResponse)
def kpis(
    _: User = Depends(DASHBOARD_ROLES),
    db: Session = Depends(get_db),
    period_start: date | None = None,
    period_end: date | None = None,
    department_id: int | None = None,
    employee_type: EmployeeType | None = None,
):
    """{total_net_salary_paid (paid only), payslips_generated, average_salary,
    approved_time_off_days, attendance_health_pct}."""
    return service.get_kpis(
        db, period_start, period_end, department_id, employee_type
    )


@router.get("/salary-by-department", response_model=list[SalaryByDepartmentItem])
def salary_by_department(
    _: User = Depends(DASHBOARD_ROLES),
    db: Session = Depends(get_db),
    period_start: date | None = None,
    period_end: date | None = None,
    department_id: int | None = None,
    employee_type: EmployeeType | None = None,
):
    """Bar chart: [{department_name, total_salary (paid net), headcount}]."""
    return service.get_salary_by_department(
        db, period_start, period_end, department_id, employee_type
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
):
    """Line chart: last N months of PAID payruns (missing months -> 0)."""
    return service.get_monthly_net_salary_trend(
        db, months, period_start, period_end, department_id, employee_type
    )


@router.get("/attendance-overview", response_model=AttendanceOverview)
def attendance_overview(
    _: User = Depends(DASHBOARD_ROLES),
    db: Session = Depends(get_db),
    period_start: date | None = None,
    period_end: date | None = None,
    department_id: int | None = None,
    employee_type: EmployeeType | None = None,
):
    """{present, late, absent (computed), overtime, missing_checkouts,
    manual_edits, coverage_pct}."""
    return service.get_attendance_overview(
        db, period_start, period_end, department_id, employee_type
    )


@router.get("/time-off-overview", response_model=TimeOffOverview)
def time_off_overview(
    _: User = Depends(DASHBOARD_ROLES),
    db: Session = Depends(get_db),
    period_start: date | None = None,
    period_end: date | None = None,
    department_id: int | None = None,
    employee_type: EmployeeType | None = None,
):
    """{approved_days, pending_requests, balances_by_type} — balances come
    from the live v_time_off_balances view."""
    return service.get_time_off_overview(
        db, period_start, period_end, department_id, employee_type
    )


@router.get("/payroll-alerts", response_model=PayrollAlertsResponse)
def payroll_alerts(
    _: User = Depends(DASHBOARD_ROLES),
    db: Session = Depends(get_db),
    period_start: date | None = None,
    period_end: date | None = None,
    department_id: int | None = None,
    employee_type: EmployeeType | None = None,
):
    """Open warnings across draft/computed payslips, grouped by type with
    counts + drill-down payslip ids."""
    return service.get_payroll_alerts(
        db, period_start, period_end, department_id, employee_type
    )