"""Attendance + Time Off endpoints (Ambuj's slice).

OWNER: Ambuj. Routers stay thin (arch doc §4.3): parse query/body -> call ONE
service function -> return. All business rules live in service.py.

Route paths are registered relative to main.py's `/api/v1` prefix:
    /attendance, /time-off/types, /time-off/allocations,
    /time-off/balances, /time-off/requests

RBAC summary (see service.py for the object-level half of enforcement):
- EMPLOYEE        : self check-in/out, own time-off requests, read own
                    attendance + balances. No approve/refuse rights, and the
                    service forces every read to their own employee_id.
- HR_MANAGER + HR_PAYROLL_* + ADMIN (here `HR_ROLES`): full CRUD everywhere
                    + approve/refuse requests and allocations. HR_MANAGER's
                    payroll exclusion is Steve's concern, not ours.

Static path segments (/me, /check-in, /sweep-missing-checkouts, ...) are
declared BEFORE the /{id}-style routes so FastAPI never tries to parse them
as integer ids.
"""

from datetime import date

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.dependencies import get_current_user, require_roles
from app.models.auth import User
from app.models.enums import (
    AllocationStatus,
    AttendanceStatus,
    TimeOffRequestStatus,
    TimeOffUnit,
)
from app.schemas.attendance_timeoff import (
    AttendanceCheckInCreate,
    AttendanceManualCreate,
    AttendanceRead,
    AttendanceSummaryRead,
    AttendanceUpdate,
    MissingCheckoutSweepResult,
    Page,
    TimeOffAllocationCreate,
    TimeOffAllocationRead,
    TimeOffBalanceRead,
    TimeOffRequestCreate,
    TimeOffRequestRead,
    TimeOffTypeCreate,
    TimeOffTypeRead,
    TimeOffTypeUpdate,
)

from . import service

router = APIRouter()

# Attendance / time-off write+approve power: every role except plain EMPLOYEE.
HR_ONLY = require_roles(
    "HR_MANAGER", "HR_PAYROLL_USER", "HR_PAYROLL_MANAGER", "ADMIN"
)


# ---------------------------------------------------------------------------
# Attendance — reads
# ---------------------------------------------------------------------------


@router.get("/attendance", response_model=Page[AttendanceRead])
def list_attendance(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    employee_id: int | None = None,
    status_filter: AttendanceStatus | None = Query(default=None, alias="status"),
    date_from: date | None = None,
    date_to: date | None = None,
    is_manual_correction: bool | None = None,
):
    """List attendance. HR filters anyone; EMPLOYEE is scoped to their own
    records (asking for another employee_id -> 403)."""
    return service.list_attendance(
        db, current_user, page=page, page_size=page_size,
        employee_id=employee_id, status=status_filter,
        date_from=date_from, date_to=date_to,
        is_manual_correction=is_manual_correction,
    )


@router.get("/attendance/me", response_model=Page[AttendanceRead])
def my_attendance(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    date_from: date | None = None,
    date_to: date | None = None,
):
    """Current employee's own attendance (EMPLOYEE self-service). Declared
    BEFORE /attendance/{id} so 'me' is never parsed as an id."""
    return service.list_attendance(
        db, current_user, page=page, page_size=page_size,
        employee_id=current_user.employee_id,
        date_from=date_from, date_to=date_to,
    )


@router.post(
    "/attendance/sweep-missing-checkouts",
    response_model=MissingCheckoutSweepResult,
)
def sweep_missing_checkouts(
    _: User = Depends(HR_ONLY),
    db: Session = Depends(get_db),
):
    """Manual end-of-day sweep: stamps open entries whose expected EOD + grace
    has passed as missing_checkout (the scheduled-job equivalent)."""
    return service.sweep_missing_checkouts(db)


@router.get("/attendance/{attendance_id}", response_model=AttendanceRead)
def get_attendance(
    attendance_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """HR: any record. EMPLOYEE: own records only (403 otherwise)."""
    return service.get_attendance(db, current_user, attendance_id)


@router.get("/attendance/{employee_id}/summary", response_model=AttendanceSummaryRead)
def attendance_summary(
    employee_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    date_from: date | None = None,
    date_to: date | None = None,
):
    """Aggregates for the smart-button / dashboard. HR: any employee;
    EMPLOYEE: own only (403 otherwise). Absent is derived from the schedule
    (no synthetic rows in the attendance table)."""
    return service.get_attendance_summary(
        db, current_user, employee_id, date_from=date_from, date_to=date_to
    )


# ---------------------------------------------------------------------------
# Attendance — check-in / check-out
# ---------------------------------------------------------------------------


@router.post(
    "/attendance/check-in",
    response_model=AttendanceRead,
    status_code=status.HTTP_201_CREATED,
)
def check_in(
    payload: AttendanceCheckInCreate | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Open a shift. EMPLOYEE: for themselves (no employee_id needed — forced
    to their linked row); HR may pass employee_id to log on someone's behalf.
    Body is optional (empty body == check in right now)."""
    body = payload or AttendanceCheckInCreate()
    return service.check_in(db, current_user, body)


@router.post(
    "/attendance/{attendance_id}/check-out",
    response_model=AttendanceRead,
)
def check_out(
    attendance_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Close an open shift — sets check_out=now(), computes worked_hours and
    the final status. EMPLOYEE: their own entry; HR: anyone's."""
    return service.check_out(db, current_user, attendance_id)


# ---------------------------------------------------------------------------
# Attendance — manual entry & corrections (HR only)
# ---------------------------------------------------------------------------


@router.post(
    "/attendance",
    response_model=AttendanceRead,
    status_code=status.HTTP_201_CREATED,
)
def create_manual_attendance(
    payload: AttendanceManualCreate,
    current_user: User = Depends(HR_ONLY),
    db: Session = Depends(get_db),
):
    """HR-only direct manual entry (both check_in & check_out given). Stamps
    is_manual_correction=true + corrected_by_user_id."""
    return service.create_manual_attendance(db, current_user, payload)


@router.patch("/attendance/{attendance_id}", response_model=AttendanceRead)
def update_attendance(
    attendance_id: int,
    payload: AttendanceUpdate,
    current_user: User = Depends(HR_ONLY),
    db: Session = Depends(get_db),
):
    """HR-only correction of an existing entry — same manual-correction
    stamping; worked_hours/status are re-derived from the merged times."""
    return service.update_attendance(db, current_user, attendance_id, payload)


# ---------------------------------------------------------------------------
# Time Off Types — HR writes, everyone reads (request form dropdown)
# ---------------------------------------------------------------------------


@router.get("/time-off/types", response_model=Page[TimeOffTypeRead])
def list_time_off_types(
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    is_active: bool | None = None,
    unit: TimeOffUnit | None = None,
):
    return service.list_time_off_types(
        db, page=page, page_size=page_size, is_active=is_active, unit=unit
    )


@router.get("/time-off/types/{type_id}", response_model=TimeOffTypeRead)
def get_time_off_type(
    type_id: int,
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return service.get_time_off_type(db, type_id)


@router.post(
    "/time-off/types",
    response_model=TimeOffTypeRead,
    status_code=status.HTTP_201_CREATED,
)
def create_time_off_type(
    payload: TimeOffTypeCreate,
    _: User = Depends(HR_ONLY),
    db: Session = Depends(get_db),
):
    return service.create_time_off_type(db, payload)


@router.patch("/time-off/types/{type_id}", response_model=TimeOffTypeRead)
def update_time_off_type(
    type_id: int,
    payload: TimeOffTypeUpdate,
    _: User = Depends(HR_ONLY),
    db: Session = Depends(get_db),
):
    return service.update_time_off_type(db, type_id, payload)


@router.delete(
    "/time-off/types/{type_id}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_time_off_type(
    type_id: int,
    _: User = Depends(HR_ONLY),
    db: Session = Depends(get_db),
):
    """Soft delete (is_active=false); blocked while pending requests/
    allocations reference the type."""
    service.delete_time_off_type(db, type_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Time Off Allocations — HR only
# ---------------------------------------------------------------------------


@router.get("/time-off/allocations", response_model=Page[TimeOffAllocationRead])
def list_time_off_allocations(
    _: User = Depends(HR_ONLY),
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    employee_id: int | None = None,
    time_off_type_id: int | None = None,
    status_filter: AllocationStatus | None = Query(default=None, alias="status"),
):
    return service.list_time_off_allocations(
        db, page=page, page_size=page_size, employee_id=employee_id,
        time_off_type_id=time_off_type_id, status=status_filter,
    )


@router.post(
    "/time-off/allocations",
    response_model=TimeOffAllocationRead,
    status_code=status.HTTP_201_CREATED,
)
def create_time_off_allocation(
    payload: TimeOffAllocationCreate,
    current_user: User = Depends(HR_ONLY),
    db: Session = Depends(get_db),
):
    """HR grant — created as `to_approve`, then approved/refused via the
    approve/refuse actions below."""
    return service.create_time_off_allocation(db, current_user, payload)


@router.post(
    "/time-off/allocations/{allocation_id}/approve",
    response_model=TimeOffAllocationRead,
)
def approve_time_off_allocation(
    allocation_id: int,
    current_user: User = Depends(HR_ONLY),
    db: Session = Depends(get_db),
):
    return service.approve_time_off_allocation(db, current_user, allocation_id)


@router.post(
    "/time-off/allocations/{allocation_id}/refuse",
    response_model=TimeOffAllocationRead,
)
def refuse_time_off_allocation(
    allocation_id: int,
    current_user: User = Depends(HR_ONLY),
    db: Session = Depends(get_db),
):
    return service.refuse_time_off_allocation(db, current_user, allocation_id)


# ---------------------------------------------------------------------------
# Balances — HR sees anyone's; EMPLOYEE only /balances/me
# ---------------------------------------------------------------------------


@router.get("/time-off/balances", response_model=list[TimeOffBalanceRead])
def list_time_off_balances(
    _: User = Depends(HR_ONLY),
    db: Session = Depends(get_db),
    employee_id: int | None = None,
    time_off_type_id: int | None = None,
):
    """Live balance view (allocated - taken) per employee + type. Pairs with
    no allocation/request history don't appear."""
    return service.list_time_off_balances(
        db, employee_id=employee_id, time_off_type_id=time_off_type_id
    )


@router.get("/time-off/balances/me", response_model=list[TimeOffBalanceRead])
def my_balances(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Current employee's own balances. Declared BEFORE /time-off/balances so
    the 'me' segment is not treated as a query on the un-prefixed path."""
    return service.get_my_balances(db, current_user)


# ---------------------------------------------------------------------------
# Time Off Requests
# ---------------------------------------------------------------------------


@router.get("/time-off/requests", response_model=Page[TimeOffRequestRead])
def list_time_off_requests(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    employee_id: int | None = None,
    status_filter: TimeOffRequestStatus | None = Query(default=None, alias="status"),
    time_off_type_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
):
    """HR: full filter set. EMPLOYEE: forced to own requests (asking for
    another employee_id -> 403)."""
    return service.list_time_off_requests(
        db, current_user, page=page, page_size=page_size,
        employee_id=employee_id, status=status_filter,
        time_off_type_id=time_off_type_id, date_from=date_from, date_to=date_to,
    )


@router.get("/time-off/requests/{request_id}", response_model=TimeOffRequestRead)
def get_time_off_request(
    request_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """HR: any request. EMPLOYEE: own requests only (403 otherwise)."""
    return service.get_time_off_request(db, current_user, request_id)


@router.post(
    "/time-off/requests",
    response_model=TimeOffRequestRead,
    status_code=status.HTTP_201_CREATED,
)
def create_time_off_request(
    payload: TimeOffRequestCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """EMPLOYEE submits their own (employee_id forced); HR may create on
    behalf of anyone."""
    return service.create_time_off_request(db, current_user, payload)


@router.post(
    "/time-off/requests/{request_id}/approve",
    response_model=TimeOffRequestRead,
)
def approve_time_off_request(
    request_id: int,
    current_user: User = Depends(HR_ONLY),
    db: Session = Depends(get_db),
):
    """HR approve — guards overlap + live balance (single transaction)."""
    return service.approve_time_off_request(db, current_user, request_id)


@router.post(
    "/time-off/requests/{request_id}/refuse",
    response_model=TimeOffRequestRead,
)
def refuse_time_off_request(
    request_id: int,
    current_user: User = Depends(HR_ONLY),
    db: Session = Depends(get_db),
):
    return service.refuse_time_off_request(db, current_user, request_id)


@router.post(
    "/time-off/requests/{request_id}/cancel",
    response_model=TimeOffRequestRead,
)
def cancel_time_off_request(
    request_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Requester cancels their own to_approve request; HR may also cancel an
    APPROVED request, but only before the leave's date_from has passed."""
    return service.cancel_time_off_request(db, current_user, request_id)
