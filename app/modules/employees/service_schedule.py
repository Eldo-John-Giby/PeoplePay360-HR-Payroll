"""Working Schedule service layer (OWNER: Ameen).

Business rules (spec §2.3):
- `total_weekly_hours` is DERIVED (never stored): computed by the pure
  function `compute_total_weekly_hours` in service.py (list + detail views).
- Lines are validated server-side: day 0-6, end>start, no same-day overlap.
- POST creates schedule + lines atomically; PUT /lines REPLACES the full set
  in one transaction (no partial-update ordering bugs).
- A zero-line schedule may exist (draft-ish, total_weekly_hours=0) but cannot
  be assigned to an Employee/Contract ("assign at least one working day
  first").
- Soft delete: reject 409 if referenced by active employees / active
  contracts.
"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.exceptions import ConflictException, ValidationException
from app.models.employee import Contract, Employee
from app.models.enums import ContractStatus, EmployeeStatus
from app.models.organization import WorkingSchedule, WorkingScheduleLine
from app.schemas.employee import (
    WorkingScheduleCreate,
    WorkingScheduleLineCreate,
    WorkingScheduleUpdate,
)

from .service import (
    compute_total_weekly_hours,
    count_rows,
    get_or_404,
    paginate,
    require_active,
    validate_schedule_lines,
)


def _schedule_item(db: Session, sched: WorkingSchedule) -> dict:
    """Serialization helper — `total_weekly_hours` goes through the pure
    function (Decimal, 2dp) instead of the ORM float property, so the JSON
    is consistent ("40.00", not "40.0"). Requires sched.lines to be loaded.
    """
    return {
        "id": sched.id,
        "name": sched.name,
        "schedule_type": sched.schedule_type,
        "company_id": sched.company_id,
        "is_active": sched.is_active,
        "total_weekly_hours": compute_total_weekly_hours(list(sched.lines)),
        "created_at": sched.created_at,
        "updated_at": sched.updated_at,
    }


def _schedule_detail(db: Session, sched: WorkingSchedule) -> dict:
    item = _schedule_item(db, sched)
    item["lines"] = list(sched.lines)
    return item


def validate_schedule_assignable(db: Session, schedule_id: int) -> WorkingSchedule:
    """A schedule must be active AND have >= 1 working day before it can be
    assigned to an Employee or Contract (spec §2.3 zero-lines edge case)."""
    sched = require_active(db, WorkingSchedule, schedule_id, "Working schedule")
    has_lines = db.scalar(
        select(WorkingScheduleLine.id)
        .where(WorkingScheduleLine.working_schedule_id == schedule_id)
        .limit(1)
    )
    if not has_lines:
        raise ValidationException(
            f"Working schedule '{sched.name}' has no working days — assign "
            "at least one working day first."
        )
    return sched


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

def list_working_schedules(
    db: Session,
    page: int | None,
    page_size: int | None,
    search: str | None,
    is_active: bool | None,
) -> tuple[list[dict], int, int, int]:
    page, page_size = paginate(page, page_size)
    stmt = select(WorkingSchedule).options(selectinload(WorkingSchedule.lines))
    if search:
        stmt = stmt.where(WorkingSchedule.name.ilike(f"%{search}%"))
    if is_active is not None:
        stmt = stmt.where(WorkingSchedule.is_active.is_(is_active))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(WorkingSchedule.name)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return [_schedule_item(db, s) for s in rows], total, page, page_size


def get_working_schedule(db: Session, schedule_id: int) -> dict:
    from app.core.exceptions import NotFoundException

    sched = db.scalar(
        select(WorkingSchedule)
        .options(selectinload(WorkingSchedule.lines))
        .where(WorkingSchedule.id == schedule_id)
    )
    if sched is None:
        raise NotFoundException(f"Working schedule {schedule_id} not found.")
    return _schedule_detail(db, sched)


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------

def create_working_schedule(
    db: Session, payload: WorkingScheduleCreate
) -> dict:
    validate_schedule_lines(payload.lines)
    sched = WorkingSchedule(
        name=payload.name,
        schedule_type=payload.schedule_type,
        company_id=payload.company_id,
        is_active=payload.is_active,
    )
    db.add(sched)
    db.flush()
    for line in payload.lines:
        sched.lines.append(WorkingScheduleLine(**line.model_dump()))
    db.commit()
    db.refresh(sched)
    return _schedule_detail(db, sched)


def update_working_schedule(
    db: Session, schedule_id: int, payload: WorkingScheduleUpdate
) -> dict:
    sched = get_or_404(db, WorkingSchedule, schedule_id, "Working schedule")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(sched, field, value)
    db.commit()
    db.refresh(sched)
    return _schedule_detail(db, sched)


def replace_schedule_lines(
    db: Session,
    schedule_id: int,
    lines: list[WorkingScheduleLineCreate],
) -> WorkingSchedule:
    """Replace the FULL set of lines in one transaction.

    Validation happens BEFORE any mutation, so a bad payload leaves the
    schedule untouched.
    """
    sched = get_or_404(db, WorkingSchedule, schedule_id, "Working schedule")
    validate_schedule_lines(lines)
    sched.lines = [WorkingScheduleLine(**ln.model_dump()) for ln in lines]
    db.commit()
    db.refresh(sched)
    return _schedule_detail(db, sched)


def soft_delete_working_schedule(db: Session, schedule_id: int) -> None:
    """Soft delete: is_active=false. 409 (with counts) if still referenced."""
    sched = get_or_404(db, WorkingSchedule, schedule_id, "Working schedule")
    employee_count = count_rows(
        db, Employee, Employee.working_schedule_id == sched.id,
        Employee.status == EmployeeStatus.active,
    )
    contract_count = count_rows(
        db, Contract, Contract.working_schedule_id == sched.id,
        Contract.status.in_([ContractStatus.draft, ContractStatus.running]),
    )
    if employee_count or contract_count:
        raise ConflictException(
            f"Cannot deactivate working schedule '{sched.name}': it is "
            f"referenced by {employee_count} active employee(s) and "
            f"{contract_count} active contract(s)."
        )
    sched.is_active = False
    db.commit()