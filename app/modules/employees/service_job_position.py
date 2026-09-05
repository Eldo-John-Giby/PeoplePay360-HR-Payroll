"""Job Position service layer (OWNER: Ameen).

Business rules (spec §2.2):
- Scoped to a department (?department_id=); department must exist & be active
- Duplicate (title, department_id)        -> 409
- Soft delete (is_active=false); reject 409 with counts if active employees
  are still assigned to the position
"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.exceptions import ConflictException
from app.models.employee import Employee
from app.models.organization import Department, JobPosition
from app.schemas.employee import JobPositionCreate, JobPositionUpdate

from .service import count_rows, get_or_404, paginate, require_active


def _find_duplicate(
    db: Session, title: str, department_id: int, exclude_id: int | None
) -> bool:
    stmt = select(JobPosition.id).where(
        JobPosition.title == title,
        JobPosition.department_id == department_id,
    )
    if exclude_id is not None:
        stmt = stmt.where(JobPosition.id != exclude_id)
    return db.scalar(stmt) is not None


def _validate_department(db: Session, department_id: int) -> None:
    require_active(db, Department, department_id, "Department")


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

def list_job_positions(
    db: Session,
    page: int | None,
    page_size: int | None,
    search: str | None,
    department_id: int | None,
    is_active: bool | None,
) -> tuple[list[JobPosition], int, int, int]:
    page, page_size = paginate(page, page_size)
    stmt = select(JobPosition)
    if search:
        stmt = stmt.where(JobPosition.title.ilike(f"%{search}%"))
    if department_id is not None:
        stmt = stmt.where(JobPosition.department_id == department_id)
    if is_active is not None:
        stmt = stmt.where(JobPosition.is_active.is_(is_active))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.options(selectinload(JobPosition.department))
        .order_by(JobPosition.title)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return list(rows), total, page, page_size


def get_job_position(db: Session, job_position_id: int) -> JobPosition:
    return get_or_404(db, JobPosition, job_position_id, "Job position")


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------

def create_job_position(
    db: Session, payload: JobPositionCreate
) -> JobPosition:
    _validate_department(db, payload.department_id)
    if _find_duplicate(
        db, payload.title, payload.department_id, None
    ):
        raise ConflictException(
            f"Job position '{payload.title}' already exists in department "
            f"{payload.department_id}."
        )
    pos = JobPosition(**payload.model_dump())
    db.add(pos)
    db.commit()
    db.refresh(pos)
    return pos


def update_job_position(
    db: Session, job_position_id: int, payload: JobPositionUpdate
) -> JobPosition:
    pos = get_or_404(db, JobPosition, job_position_id, "Job position")
    data = payload.model_dump(exclude_unset=True)

    if "department_id" in data:
        _validate_department(db, data["department_id"])

    new_title = data.get("title", pos.title)
    new_dept = data.get("department_id", pos.department_id)
    if new_title != pos.title and _find_duplicate(
        db, new_title, new_dept, pos.id
    ):
        raise ConflictException(
            f"Job position '{new_title}' already exists in department "
            f"{new_dept}."
        )

    for field, value in data.items():
        setattr(pos, field, value)
    db.commit()
    db.refresh(pos)
    return pos


def soft_delete_job_position(db: Session, job_position_id: int) -> None:
    """Soft delete: is_active=false. 409 (with counts) if employees remain."""
    pos = get_or_404(db, JobPosition, job_position_id, "Job position")
    employee_count = count_rows(
        db, Employee, Employee.job_position_id == pos.id,
        Employee.status == "active",
    )
    if employee_count:
        raise ConflictException(
            f"Cannot deactivate job position '{pos.title}': {employee_count} "
            "active employee(s) are still assigned to it. Reassign them first."
        )
    pos.is_active = False
    db.commit()