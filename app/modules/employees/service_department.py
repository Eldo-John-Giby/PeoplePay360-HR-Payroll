"""Department service layer (OWNER: Ameen).

Business rules (spec §2.1):
- Duplicate (name, company_id)            -> 409, not a raw DB error
- parent must exist & be active           -> 404 / 422
- self-reference / cycle in hierarchy     -> 422 (server-side walk, capped)
- soft delete (is_active=false); reject 409 with counts if the department
  still has active employees or active job positions
"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.exceptions import ConflictException, ValidationException
from app.models.employee import Employee
from app.models.organization import Department, JobPosition
from app.schemas.employee import DepartmentCreate, DepartmentUpdate

from .service import (
    count_rows,
    get_or_404,
    paginate,
    require_active,
    would_create_cycle,
)


def _find_duplicate(
    db: Session, name: str, company_id: int | None, exclude_id: int | None
) -> bool:
    stmt = select(Department.id).where(Department.name == name)
    if company_id is None:
        stmt = stmt.where(Department.company_id.is_(None))
    else:
        stmt = stmt.where(Department.company_id == company_id)
    if exclude_id is not None:
        stmt = stmt.where(Department.id != exclude_id)
    return db.scalar(stmt) is not None


def _validate_parent(db: Session, parent_id: int | None) -> None:
    if parent_id is None:
        return
    require_active(db, Department, parent_id, "Department")


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

def list_departments(
    db: Session,
    page: int | None,
    page_size: int | None,
    search: str | None,
    parent_id: int | None,
    is_active: bool | None,
) -> tuple[list[Department], int, int, int]:
    page, page_size = paginate(page, page_size)
    stmt = select(Department)
    if search:
        stmt = stmt.where(Department.name.ilike(f"%{search}%"))
    if parent_id is not None:
        stmt = stmt.where(Department.parent_department_id == parent_id)
    if is_active is not None:
        stmt = stmt.where(Department.is_active.is_(is_active))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(Department.name)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return list(rows), total, page, page_size


def get_department(db: Session, department_id: int) -> Department:
    return get_or_404(db, Department, department_id, "Department")


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------

def create_department(db: Session, payload: DepartmentCreate) -> Department:
    _validate_parent(db, payload.parent_department_id)
    if _find_duplicate(db, payload.name, payload.company_id, None):
        raise ConflictException(f"Department '{payload.name}' already exists.")
    dept = Department(**payload.model_dump())
    db.add(dept)
    db.commit()
    db.refresh(dept)
    return dept


def update_department(
    db: Session, department_id: int, payload: DepartmentUpdate
) -> Department:
    dept = get_or_404(db, Department, department_id, "Department")
    data = payload.model_dump(exclude_unset=True)

    if "name" in data and data["name"] != dept.name and _find_duplicate(
        db, data["name"], data.get("company_id", dept.company_id), dept.id
    ):
        raise ConflictException(f"Department '{data['name']}' already exists.")

    if "parent_department_id" in data:
        new_parent = data["parent_department_id"]
        if new_parent == dept.id:
            raise ValidationException(
                "A department cannot be its own parent."
            )
        _validate_parent(db, new_parent)
        if would_create_cycle(
            db, Department, dept.id, "parent_department_id", new_parent
        ):
            raise ValidationException(
                "This change would create a cycle in the department hierarchy."
            )

    for field, value in data.items():
        setattr(dept, field, value)
    db.commit()
    db.refresh(dept)
    return dept


def soft_delete_department(db: Session, department_id: int) -> None:
    """Soft delete: is_active=false. 409 (with counts) if still in use."""
    dept = get_or_404(db, Department, department_id, "Department")
    employee_count = count_rows(
        db, Employee, Employee.department_id == dept.id,
        Employee.status == "active",
    )
    job_position_count = count_rows(
        db, JobPosition, JobPosition.department_id == dept.id,
        JobPosition.is_active.is_(True),
    )
    if employee_count or job_position_count:
        raise ConflictException(
            f"Cannot deactivate department '{dept.name}': it still has "
            f"{employee_count} active employee(s) and "
            f"{job_position_count} active job position(s). Reassign or "
            "deactivate them first."
        )
    dept.is_active = False
    db.commit()