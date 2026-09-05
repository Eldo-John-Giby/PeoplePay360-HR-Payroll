"""Time Off service layer (part of the Attendance & Time Off module).

Covers TimeOffType (config), TimeOffAllocation (the balance GRANT side) and
TimeOffRequest (the employee-facing TAKE side) + the live-balance reads.

Business rules (architecture doc §5 + the original stub's checklist):
- The leave balance is NEVER stored: it comes from the v_time_off_balances
  SQL view (approved allocations - approved requests). Approving a request
  that consumes an allocated type checks that REMAINING balance first.
- Approving a request that overlaps an existing approved / to_approve
  request of the same employee -> 409 (double-booking, date range inclusive
  on both ends).
- Allocations flow draft -> approved | refused. Every allocation write is
  optimistically locked via version_id (StaleDataError -> 409).
- Requests flow to_approve -> approved | refused | cancelled. Employees
  cancel their own pending requests; only HR roles approve/refuse (enforced
  in the router). Types flagged requires_approval=False auto-approve.
- No hard deletes anywhere: deactivation / status transitions preserve
  history (arch doc §4.5).
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.exceptions import (
    ConflictException,
    ForbiddenException,
    NotFoundException,
    ValidationException,
)
from app.models.auth import User
from app.models.employee import Employee
from app.models.enums import (
    AllocationStatus,
    EmployeeStatus,
    TimeOffRequestStatus,
)
from app.models.timeoff import TimeOffAllocation, TimeOffRequest, TimeOffType
from app.models.views import TimeOffBalanceView
from app.modules.employees.service import get_or_404, paginate

from .service import (
    _approver_employee_id,
    _check_version,
    _commit_or_conflict,
    _inclusive_days,
    _require_employee_active,
    _scheduled_net_hours,
    _schedule_spec,
)

_ALLOC_READABLE = (AllocationStatus.draft, AllocationStatus.to_approve)


# ---------------------------------------------------------------------------
# Time Off types
# ---------------------------------------------------------------------------

def _type_dicts(rows: list[TimeOffType]) -> list[dict]:
    return [
        {
            "id": t.id,
            "name": t.name,
            "unit": t.unit,
            "requires_allocation": t.requires_allocation,
            "requires_approval": t.requires_approval,
            "affects_payroll": t.affects_payroll,
            "company_id": t.company_id,
            "is_active": t.is_active,
            "created_at": t.created_at,
            "updated_at": t.updated_at,
        }
        for t in rows
    ]


def list_time_off_types(
    db: Session,
    page: int | None,
    page_size: int | None,
    is_active: bool | None,
) -> tuple[list[dict], int, int, int]:
    page, page_size = paginate(page, page_size)
    stmt = select(TimeOffType)
    if is_active is not None:
        stmt = stmt.where(TimeOffType.is_active == is_active)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(TimeOffType.name)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return _type_dicts(list(rows)), total, page, page_size


def get_time_off_type(db: Session, type_id: int) -> dict:
    row = get_or_404(db, TimeOffType, type_id, "Time-off type")
    return _type_dicts([row])[0]


def create_time_off_type(db: Session, payload) -> dict:
    _check_type_name_unique(db, payload.name, None)
    row = TimeOffType(**payload.model_dump())
    db.add(row)
    _commit_or_conflict(db)
    db.refresh(row)
    return _type_dicts([row])[0]


def update_time_off_type(db: Session, type_id: int, payload) -> dict:
    row = get_or_404(db, TimeOffType, type_id, "Time-off type")
    data = payload.model_dump(exclude_unset=True)
    if "name" in data and data["name"] != row.name:
        _check_type_name_unique(db, data["name"], type_id)
    for field, value in data.items():
        setattr(row, field, value)
    _commit_or_conflict(db)
    db.refresh(row)
    return _type_dicts([row])[0]


def _check_type_name_unique(
    db: Session, name: str, exclude_id: int | None
) -> None:
    stmt = select(TimeOffType).where(TimeOffType.name == name)
    if exclude_id is not None:
        stmt = stmt.where(TimeOffType.id != exclude_id)
    if db.scalar(stmt) is not None:
        raise ConflictException(
            f"A time-off type named '{name}' already exists."
        )


# ---------------------------------------------------------------------------
# Time Off allocations (balance GRANT side)
# ---------------------------------------------------------------------------

def _emp_ref(emp):
    if emp is None:
        return None
    return {"id": emp.id, "full_name": emp.full_name, "work_email": emp.work_email}


def _alloc_reads(db: Session, rows: list[TimeOffAllocation]) -> list[dict]:
    if not rows:
        return []
    emp_ids = {a.employee_id for a in rows}
    type_ids = {a.time_off_type_id for a in rows}
    approver_ids = {a.approver_id for a in rows if a.approver_id is not None}
    employees = {
        e.id: e
        for e in db.scalars(
            select(Employee).where(Employee.id.in_(emp_ids))
        )
    }
    approvers = {
        e.id: e
        for e in db.scalars(
            select(Employee).where(Employee.id.in_(approver_ids))
        )
    }
    types = {
        t.id: t
        for t in db.scalars(
            select(TimeOffType).where(TimeOffType.id.in_(type_ids))
        )
    }
    out = []
    for a in rows:
        t = types.get(a.time_off_type_id)
        out.append({
            "id": a.id,
            "employee": _emp_ref(employees.get(a.employee_id)),
            "time_off_type": (
                {"id": t.id, "name": t.name, "unit": t.unit} if t else None
            ),
            "allocated_amount": a.allocated_amount,
            "valid_from": a.valid_from,
            "valid_to": a.valid_to,
            "status": a.status,
            "approver": _emp_ref(approvers.get(a.approver_id)),
            "version_id": a.version_id,
            "created_at": a.created_at,
            "updated_at": a.updated_at,
        })
    return out


def _alloc_read(db: Session, alloc: TimeOffAllocation) -> dict:
    return _alloc_reads(db, [alloc])[0]


def list_allocations(
    db: Session,
    page: int | None,
    page_size: int | None,
    employee_id: int | None,
    type_id: int | None,
    status: AllocationStatus | None,
) -> tuple[list[dict], int, int, int]:
    page, page_size = paginate(page, page_size)
    stmt = select(TimeOffAllocation)
    if employee_id is not None:
        stmt = stmt.where(TimeOffAllocation.employee_id == employee_id)
    if type_id is not None:
        stmt = stmt.where(TimeOffAllocation.time_off_type_id == type_id)
    if status is not None:
        stmt = stmt.where(TimeOffAllocation.status == status)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(
            TimeOffAllocation.valid_from.desc(), TimeOffAllocation.id.desc()
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return _alloc_reads(db, list(rows)), total, page, page_size


def get_allocation(db: Session, allocation_id: int) -> dict:
    row = get_or_404(db, TimeOffAllocation, allocation_id, "Allocation")
    return _alloc_read(db, row)


def create_allocation(db: Session, payload) -> dict:
    """HR grants a balance. New allocations start `draft`; they must be
    approved before they count toward the balance (the SQL view only sums
    status='approved' rows)."""
    employee = _require_employee_active(db, payload.employee_id)
    type_row = get_or_404(
        db, TimeOffType, payload.time_off_type_id, "Time-off type"
    )
    if not type_row.is_active:
        raise ValidationException(
            f"Time-off type '{type_row.name}' is inactive."
        )
    if not type_row.requires_allocation:
        raise ValidationException(
            f"'{type_row.name}' does not require allocation — employees "
            "request it directly."
        )
    if payload.valid_to is not None and payload.valid_to < payload.valid_from:
        raise ValidationException("valid_to must be on or after valid_from.")
    if _overlapping_allocation(
        db, employee.id, type_row.id, payload.valid_from, payload.valid_to
    ) is not None:
        raise ConflictException(
            "This employee already has an allocation for the same period of "
            "this type — adjust the dates or update the existing allocation."
        )

    row = TimeOffAllocation(
        employee_id=employee.id,
        time_off_type_id=type_row.id,
        allocated_amount=payload.allocated_amount,
        valid_from=payload.valid_from,
        valid_to=payload.valid_to,
        status=AllocationStatus.draft,
    )
    db.add(row)
    _commit_or_conflict(db)
    db.refresh(row)
    return _alloc_read(db, row)


def _overlapping_allocation(
    db: Session,
    employee_id: int,
    type_id: int,
    valid_from: date,
    valid_to: date | None,
    exclude_id: int | None = None,
) -> TimeOffAllocation | None:
    """Existing non-refused allocation whose validity range overlaps."""
    stmt = select(TimeOffAllocation).where(
        TimeOffAllocation.employee_id == employee_id,
        TimeOffAllocation.time_off_type_id == type_id,
        TimeOffAllocation.status != AllocationStatus.refused,
    )
    if exclude_id is not None:
        stmt = stmt.where(TimeOffAllocation.id != exclude_id)
    eff_to = valid_to or date.max
    for row in db.scalars(stmt):
        row_to = row.valid_to or date.max
        if row.valid_from <= eff_to and row_to >= valid_from:
            return row
    return None


def update_allocation(db: Session, allocation_id: int, payload) -> dict:
    """Edit a draft/to_approve allocation only (approved/refused are history
    — refuse + recreate instead of editing a finalized grant)."""
    row = get_or_404(db, TimeOffAllocation, allocation_id, "Allocation")
    _check_version(row, payload.version_id)
    if row.status not in _ALLOC_READABLE:
        raise ConflictException(
            f"Only draft/to_approve allocations can be edited (this one is "
            f"'{row.status.value}'). Refuse it and create a new allocation "
            "if the grant was wrong."
        )
    data = payload.model_dump(exclude_unset=True)
    data.pop("version_id", None)
    type_id = row.time_off_type_id
    if "time_off_type_id" in data:
        type_id = get_or_404(
            db, TimeOffType, data["time_off_type_id"], "Time-off type"
        ).id
    valid_from = data.get("valid_from", row.valid_from)
    valid_to = data.get("valid_to", row.valid_to)  # explicit-null clears
    if valid_to is not None and valid_to < valid_from:
        raise ValidationException("valid_to must be on or after valid_from.")
    if _overlapping_allocation(
        db, row.employee_id, type_id, valid_from, valid_to, exclude_id=row.id
    ) is not None:
        raise ConflictException(
            "This edit overlaps another allocation for the same employee and "
            "period."
        )
    for field, value in data.items():
        setattr(row, field, value)
    _commit_or_conflict(db)
    db.refresh(row)
    return _alloc_read(db, row)


def approve_allocation(
    db: Session, allocation_id: int, payload, current_user: User
) -> dict:
    """draft|to_approve -> approved. Only approved allocations count toward
    the leave balance (the view filters status='approved')."""
    row = get_or_404(db, TimeOffAllocation, allocation_id, "Allocation")
    _check_version(row, payload.version_id)
    if row.status == AllocationStatus.approved:
        raise ConflictException("This allocation is already approved.")
    if row.status == AllocationStatus.refused:
        raise ConflictException(
            "A refused allocation cannot be approved — create a new one."
        )
    row.status = AllocationStatus.approved
    row.approver_id = _approver_employee_id(db, current_user)
    _commit_or_conflict(db)
    db.refresh(row)
    return _alloc_read(db, row)


def refuse_allocation(
    db: Session, allocation_id: int, payload, current_user: User
) -> dict:
    row = get_or_404(db, TimeOffAllocation, allocation_id, "Allocation")
    _check_version(row, payload.version_id)
    if row.status not in _ALLOC_READABLE:
        raise ConflictException(
            f"Only draft/to_approve allocations can be refused (this one is "
            f"'{row.status.value}')."
        )
    row.status = AllocationStatus.refused
    row.approver_id = _approver_employee_id(db, current_user)
    _commit_or_conflict(db)
    db.refresh(row)
    return _alloc_read(db, row)


# ---------------------------------------------------------------------------
# Time Off requests (the employee TAKE side)
# ---------------------------------------------------------------------------

def _request_reads(db: Session, rows: list[TimeOffRequest]) -> list[dict]:
    if not rows:
        return []
    emp_ids = {r.employee_id for r in rows}
    type_ids = {r.time_off_type_id for r in rows}
    approver_ids = {r.approver_id for r in rows if r.approver_id is not None}
    employees = {
        e.id: e
        for e in db.scalars(
            select(Employee).where(Employee.id.in_(emp_ids))
        )
    }
    approvers = {
        e.id: e
        for e in db.scalars(
            select(Employee).where(Employee.id.in_(approver_ids))
        )
    }
    types = {
        t.id: t
        for t in db.scalars(
            select(TimeOffType).where(TimeOffType.id.in_(type_ids))
        )
    }
    out = []
    for r in rows:
        t = types.get(r.time_off_type_id)
        out.append({
            "id": r.id,
            "employee": _emp_ref(employees.get(r.employee_id)),
            "time_off_type": (
                {"id": t.id, "name": t.name, "unit": t.unit} if t else None
            ),
            "date_from": r.date_from,
            "date_to": r.date_to,
            "duration": r.duration,
            "status": r.status,
            "approver": _emp_ref(approvers.get(r.approver_id)),
            "reason": r.reason,
            "created_at": r.created_at,
            "updated_at": r.updated_at,
        })
    return out


def list_requests(
    db: Session,
    page: int | None,
    page_size: int | None,
    employee_id: int | None,
    type_id: int | None,
    status: TimeOffRequestStatus | None,
    date_from: date | None,
    date_to: date | None,
) -> tuple[list[dict], int, int, int]:
    page, page_size = paginate(page, page_size)
    stmt = select(TimeOffRequest)
    if employee_id is not None:
        stmt = stmt.where(TimeOffRequest.employee_id == employee_id)
    if type_id is not None:
        stmt = stmt.where(TimeOffRequest.time_off_type_id == type_id)
    if status is not None:
        stmt = stmt.where(TimeOffRequest.status == status)
    if date_from is not None:
        stmt = stmt.where(TimeOffRequest.date_from >= date_from)
    if date_to is not None:
        stmt = stmt.where(TimeOffRequest.date_from <= date_to)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(TimeOffRequest.date_from.desc(), TimeOffRequest.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return _request_reads(db, list(rows)), total, page, page_size


def get_request(db: Session, request_id: int) -> dict:
    row = get_or_404(db, TimeOffRequest, request_id, "Time-off request")
    return _request_reads(db, [row])[0]


def create_request(
    db: Session, payload, actor_employee_id: int | None, actor_is_hr: bool
) -> dict:
    """Employee self-service request (or HR acting on an employee's behalf).
    Types flagged requires_approval=False are approved immediately."""
    target_employee_id = payload.employee_id or actor_employee_id
    if target_employee_id is None:
        raise NotFoundException(
            "No employee record is linked to this account."
        )
    if payload.employee_id is not None and not actor_is_hr:
        raise ForbiddenException(
            "You can only raise time-off requests for yourself."
        )
    employee = _require_employee_active(db, target_employee_id)
    type_row = get_or_404(
        db, TimeOffType, payload.time_off_type_id, "Time-off type"
    )
    if not type_row.is_active:
        raise ValidationException(
            f"Time-off type '{type_row.name}' is inactive."
        )
    if payload.date_to < payload.date_from:
        raise ValidationException("date_to must be on or after date_from.")

    if type_row.unit.value == "days":
        # Days are authoritative: the inclusive date span IS the duration.
        duration = Decimal(str(_inclusive_days(payload.date_from, payload.date_to)))
    elif payload.duration is not None:
        duration = payload.duration
    else:
        # Hours type without a duration: default to one scheduled day.
        line = _schedule_spec(db, employee, payload.date_from)
        if line is None:
            raise ValidationException(
                "This type is measured in hours and no scheduled hours "
                "exist for the start date — supply an explicit duration."
            )
        duration = _scheduled_net_hours(line)

    auto_approved = not type_row.requires_approval
    row = TimeOffRequest(
        employee_id=employee.id,
        time_off_type_id=type_row.id,
        date_from=payload.date_from,
        date_to=payload.date_to,
        duration=duration,
        status=(
            TimeOffRequestStatus.approved
            if auto_approved else TimeOffRequestStatus.to_approve
        ),
        reason=payload.reason,
    )
    db.add(row)
    _commit_or_conflict(db)
    db.refresh(row)
    return _request_reads(db, [row])[0]


def approve_request(
    db: Session, request_id: int, current_user: User
) -> dict:
    """to_approve -> approved. Guards (each -> clean 409, never a 500):
    1. status must be to_approve (approved/refused/cancelled are final);
    2. an allocated-type request must fit the REMAINING balance (live SQL
       view: approved allocations - approved requests);
    3. must not overlap an existing approved/to_approve request of the same
       employee (double-booking)."""
    row = get_or_404(db, TimeOffRequest, request_id, "Time-off request")
    if row.status != TimeOffRequestStatus.to_approve:
        raise ConflictException(
            f"Only pending (to_approve) requests can be approved (this one "
            f"is '{row.status.value}')."
        )
    type_row = get_or_404(db, TimeOffType, row.time_off_type_id, "Time-off type")

    if type_row.requires_allocation:
        remaining = _remaining_balance(db, row.employee_id, type_row.id)
        if remaining < row.duration:
            raise ConflictException(
                f"Insufficient {type_row.name} balance — remaining "
                f"{remaining}, requested {row.duration}. Approve an "
                "allocation first."
            )
    if _overlapping_request(db, row) is not None:
        raise ConflictException(
            "This request overlaps an existing approved or pending request "
            "of the same employee."
        )

    row.status = TimeOffRequestStatus.approved
    row.approver_id = _approver_employee_id(db, current_user)
    _commit_or_conflict(db)
    db.refresh(row)
    return _request_reads(db, [row])[0]


def refuse_request(
    db: Session, request_id: int, current_user: User
) -> dict:
    row = get_or_404(db, TimeOffRequest, request_id, "Time-off request")
    if row.status != TimeOffRequestStatus.to_approve:
        raise ConflictException(
            f"Only pending (to_approve) requests can be refused (this one is "
            f"'{row.status.value}')."
        )
    row.status = TimeOffRequestStatus.refused
    row.approver_id = _approver_employee_id(db, current_user)
    _commit_or_conflict(db)
    db.refresh(row)
    return _request_reads(db, [row])[0]


def cancel_request(
    db: Session,
    request_id: int,
    actor_employee_id: int | None,
    actor_is_hr: bool,
) -> dict:
    """Owner cancels their own pending request (HR may cancel any pending)."""
    row = get_or_404(db, TimeOffRequest, request_id, "Time-off request")
    if not actor_is_hr and row.employee_id != actor_employee_id:
        raise ForbiddenException("You can only cancel your own requests.")
    if row.status not in (
        TimeOffRequestStatus.draft,
        TimeOffRequestStatus.to_approve,
    ):
        raise ConflictException(
            f"Only draft/to_approve requests can be cancelled (this one is "
            f"'{row.status.value}')."
        )
    row.status = TimeOffRequestStatus.cancelled
    _commit_or_conflict(db)
    db.refresh(row)
    return _request_reads(db, [row])[0]


def _remaining_balance(
    db: Session, employee_id: int, type_id: int
) -> Decimal:
    view_row = db.scalar(
        select(TimeOffBalanceView).where(
            TimeOffBalanceView.employee_id == employee_id,
            TimeOffBalanceView.time_off_type_id == type_id,
        )
    )
    return view_row.remaining if view_row is not None else Decimal("0")


def _overlapping_request(
    db: Session, row: TimeOffRequest
) -> TimeOffRequest | None:
    """Another approved/to_approve request of the same employee whose date
    range overlaps `row`'s (inclusive on both ends)."""
    return db.scalar(
        select(TimeOffRequest).where(
            TimeOffRequest.id != row.id,
            TimeOffRequest.employee_id == row.employee_id,
            TimeOffRequest.status.in_(
                [
                    TimeOffRequestStatus.approved,
                    TimeOffRequestStatus.to_approve,
                ]
            ),
            TimeOffRequest.date_from <= row.date_to,
            TimeOffRequest.date_to >= row.date_from,
        )
    )


# ---------------------------------------------------------------------------
# Balances (read-only — backed by the v_time_off_balances SQL view)
# ---------------------------------------------------------------------------

def list_balances(
    db: Session,
    page: int | None,
    page_size: int | None,
    employee_id: int | None,
    type_id: int | None,
) -> tuple[list[dict], int, int, int]:
    """Live balances from the SQL view (approved allocations - approved
    requests). Never a stored running total (architecture doc §5)."""
    page, page_size = paginate(page, page_size)
    stmt = select(TimeOffBalanceView)
    if employee_id is not None:
        stmt = stmt.where(TimeOffBalanceView.employee_id == employee_id)
    if type_id is not None:
        stmt = stmt.where(TimeOffBalanceView.time_off_type_id == type_id)
    rows = db.scalars(stmt).all()

    emp_ids = {r.employee_id for r in rows}
    type_ids = {r.time_off_type_id for r in rows}
    employees = {
        e.id: e
        for e in db.scalars(
            select(Employee).where(Employee.id.in_(emp_ids))
        )
    }
    types = {
        t.id: t
        for t in db.scalars(
            select(TimeOffType).where(TimeOffType.id.in_(type_ids))
        )
    }
    out = []
    for r in rows:
        t = types.get(r.time_off_type_id)
        out.append({
            "employee": _emp_ref(employees.get(r.employee_id)),
            "time_off_type": (
                {"id": t.id, "name": t.name, "unit": t.unit} if t else None
            ),
            "allocated": r.allocated,
            "taken": r.taken,
            "remaining": r.remaining,
        })
    total = len(out)
    start = (page - 1) * page_size
    return out[start:start + page_size], total, page, page_size
