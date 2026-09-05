"""Employee service layer (OWNER: Ameen).

Business rules (spec §2.4):
- work_email uniqueness (app-level lowercasing on write)  -> 409
- manager_id == self                                     -> 422
- manager inactive                                       -> ALLOWED, response carries warnings: ["manager is inactive"]
- circular management chain                              -> 422 (capped walk)
- inactive working_schedule / zero-line schedule         -> 422
- PATCH touches ONLY the Employee row (contracts snapshot
  department/job_position/schedule at creation — never retroactively changed)
- EMPLOYEE role: list blocked (403, router), own record only (403 otherwise)
- Kanban `?group_by=status|department` returns the full filtered set grouped,
  so column counts are exact (kanban needs the whole board, not a page)
"""

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, aliased

from app.core.exceptions import (
    ConflictException,
    ForbiddenException,
    NotFoundException,
    ValidationException,
)
from app.models.attendance import Attendance
from app.models.auth import User
from app.models.employee import Contract, Employee
from app.models.enums import EmployeeStatus
from app.models.organization import (
    Department,
    JobPosition,
    WorkingSchedule,
    WorkingScheduleLine,
)
from app.models.timeoff import TimeOffAllocation, TimeOffRequest
from app.schemas.employee import EmployeeCreate, EmployeeUpdate

from .service import (
    compute_total_weekly_hours,
    count_rows,
    get_or_404,
    has_hr_access,
    paginate,
    require_active,
    would_create_cycle,
)
from .service_contract import get_contracts_for_employee
from .service_schedule import validate_schedule_assignable

SORTABLE_FIELDS = {
    "id", "full_name", "work_email", "status", "employee_type",
    "date_of_joining", "created_at",
}


# ---------------------------------------------------------------------------
# Access control
# ---------------------------------------------------------------------------

def _resolve_employee_for_read(
    db: Session, employee_id: int | None, current_user: User
) -> Employee:
    """EMPLOYEE role may only read their own record (403 otherwise)."""
    if employee_id is None:
        raise NotFoundException(
            "The logged-in user has no linked employee record."
        )
    emp = get_or_404(db, Employee, employee_id, "Employee")
    if not has_hr_access(current_user) and current_user.employee_id != employee_id:
        raise ForbiddenException(
            "You can only access your own employee record."
        )
    return emp


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------

def _to_list_item(
    emp: Employee, dept: Department, pos: JobPosition, manager: Employee | None
) -> dict:
    return {
        "id": emp.id,
        "full_name": emp.full_name,
        "work_email": emp.work_email,
        "phone": emp.phone,
        "department": (
            {"id": dept.id, "name": dept.name, "is_active": dept.is_active}
            if dept else None
        ),
        "job_position": (
            {"id": pos.id, "title": pos.title, "is_active": pos.is_active}
            if pos else None
        ),
        "manager": (
            {"id": manager.id, "full_name": manager.full_name}
            if manager else None
        ),
        "employee_type": emp.employee_type,
        "status": emp.status,
        "date_of_joining": emp.date_of_joining,
        "work_location": emp.work_location,
    }


def related_summary(db: Session, employee_id: int) -> dict:
    """Smart-button badge counts (wireframe)."""
    get_or_404(db, Employee, employee_id, "Employee")
    return {
        "contracts_count": count_rows(
            db, Contract, Contract.employee_id == employee_id
        ),
        "attendance_count": count_rows(
            db, Attendance, Attendance.employee_id == employee_id
        ),
        "time_off_count": count_rows(
            db, TimeOffRequest, TimeOffRequest.employee_id == employee_id
        ),
        "allocations_count": count_rows(
            db, TimeOffAllocation, TimeOffAllocation.employee_id == employee_id
        ),
    }


def _build_detail(db: Session, emp: Employee) -> dict:
    """Full Form payload: identity + nested summaries + smart-button counts
    + warnings. Nested objects are fetched explicitly (Employee has no ORM
    relationships for department/job/schedule) — single employee, ~6 queries."""
    dept = db.get(Department, emp.department_id)
    pos = db.get(JobPosition, emp.job_position_id)
    manager = db.get(Employee, emp.manager_id) if emp.manager_id else None
    sched = db.get(WorkingSchedule, emp.working_schedule_id)
    sched_lines: list[WorkingScheduleLine] = []
    if sched:
        sched_lines = list(
            db.scalars(
                select(WorkingScheduleLine).where(
                    WorkingScheduleLine.working_schedule_id == sched.id
                )
            )
        )

    warnings: list[str] = []
    if manager and manager.status != EmployeeStatus.active:
        warnings.append("manager is inactive")

    return {
        "id": emp.id,
        "full_name": emp.full_name,
        "work_email": emp.work_email,
        "phone": emp.phone,
        "department": (
            {"id": dept.id, "name": dept.name, "is_active": dept.is_active}
            if dept else None
        ),
        "job_position": (
            {"id": pos.id, "title": pos.title, "is_active": pos.is_active}
            if pos else None
        ),
        "manager": (
            {"id": manager.id, "full_name": manager.full_name}
            if manager else None
        ),
        "working_schedule": (
            {
                "id": sched.id,
                "name": sched.name,
                "schedule_type": sched.schedule_type,
                "total_weekly_hours": compute_total_weekly_hours(sched_lines),
            }
            if sched else None
        ),
        "employee_type": emp.employee_type,
        "status": emp.status,
        "date_of_joining": emp.date_of_joining,
        "work_location": emp.work_location,
        "company_id": emp.company_id,
        "related": related_summary(db, emp.id),
        "warnings": warnings,
        "created_at": emp.created_at,
        "updated_at": emp.updated_at,
    }


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

def list_employees(
    db: Session,
    page: int | None,
    page_size: int | None,
    department_id: int | None,
    status: str | None,
    employee_type: str | None,
    manager_id: int | None,
    search: str | None,
    group_by: str | None,
    sort_by: str | None,
    sort_dir: str | None,
) -> tuple[list, int, int, int, bool]:
    """Returns (items_or_groups, total, page, page_size, grouped).

    Flat list honours pagination/sorting; `?group_by=status|department`
    returns the FULL filtered set grouped (exact per-column counts).
    """
    Manager = aliased(Employee)
    stmt = (
        select(Employee, Department, JobPosition, Manager)
        .join(Department, Employee.department_id == Department.id)
        .join(JobPosition, Employee.job_position_id == JobPosition.id)
        .outerjoin(Manager, Employee.manager_id == Manager.id)
    )
    if department_id is not None:
        stmt = stmt.where(Employee.department_id == department_id)
    if status is not None:
        stmt = stmt.where(Employee.status == status)
    if employee_type is not None:
        stmt = stmt.where(Employee.employee_type == employee_type)
    if manager_id is not None:
        stmt = stmt.where(Employee.manager_id == manager_id)
    if search:
        like = f"%{search}%"
        stmt = stmt.where(
            or_(
                Employee.full_name.ilike(like),
                Employee.work_email.ilike(like),
            )
        )

    page, page_size = paginate(page, page_size)

    if group_by in ("status", "department"):
        rows = db.execute(stmt).all()
        groups: dict[str, list[dict]] = {}
        for emp, dept, _pos, manager in rows:
            key = (
                emp.status.value
                if group_by == "status"
                else (dept.name if dept else "Unknown")
            )
            groups.setdefault(key, []).append(_to_list_item(emp, dept, _pos, manager))
        ordered = [
            {"key": key, "count": len(items), "items": items}
            for key, items in sorted(groups.items())
        ]
        return ordered, len(rows), page, page_size, True

    sort_field = sort_by if sort_by in SORTABLE_FIELDS else "full_name"
    col = getattr(Employee, sort_field)
    col = col.desc() if sort_dir == "desc" else col.asc()

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.execute(
        stmt.order_by(col)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    items = [_to_list_item(emp, dept, pos, manager) for emp, dept, pos, manager in rows]
    return items, total, page, page_size, False


def get_employee(db: Session, employee_id: int, current_user: User) -> dict:
    emp = _resolve_employee_for_read(db, employee_id, current_user)
    return _build_detail(db, emp)


def get_my_employee(db: Session, current_user: User) -> dict:
    """GET /employees/me — 404 if the logged-in user has no linked employee."""
    if current_user.employee_id is None:
        raise NotFoundException(
            "The logged-in user has no linked employee record."
        )
    emp = get_or_404(db, Employee, current_user.employee_id, "Employee")
    return _build_detail(db, emp)


def list_employee_contracts(
    db: Session, employee_id: int, status: str | None, current_user: User
) -> list[dict]:
    """Smart-button contracts list (EMPLOYEE role may only see their own)."""
    emp = _resolve_employee_for_read(db, employee_id, current_user)
    return get_contracts_for_employee(db, emp.id, status)


def related_summary_for(
    db: Session, employee_id: int, current_user: User
) -> dict:
    """Smart-button counts (EMPLOYEE role may only see their own)."""
    _resolve_employee_for_read(db, employee_id, current_user)
    return related_summary(db, employee_id)


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------

def create_employee(db: Session, payload: EmployeeCreate) -> dict:
    require_active(db, Department, payload.department_id, "Department")
    require_active(db, JobPosition, payload.job_position_id, "Job position")
    validate_schedule_assignable(db, payload.working_schedule_id)
    if payload.manager_id is not None:
        get_or_404(db, Employee, payload.manager_id, "Manager")

    email = payload.work_email.strip().lower()
    if db.scalar(select(Employee.id).where(Employee.work_email == email)):
        raise ConflictException(
            f"An employee with work email '{email}' already exists."
        )

    emp = Employee(
        **payload.model_dump(exclude={"work_email"}), work_email=email
    )
    db.add(emp)
    db.commit()
    db.refresh(emp)
    return _build_detail(db, emp)


def update_employee(
    db: Session, employee_id: int, payload: EmployeeUpdate
) -> dict:
    emp = get_or_404(db, Employee, employee_id, "Employee")
    data = payload.model_dump(exclude_unset=True)

    if "work_email" in data:
        email = data["work_email"].strip().lower()
        dup = db.scalar(
            select(Employee.id).where(
                Employee.work_email == email, Employee.id != emp.id
            )
        )
        if dup:
            raise ConflictException(
                f"An employee with work email '{email}' already exists."
            )
        emp.work_email = email

    if "department_id" in data:
        require_active(db, Department, data["department_id"], "Department")
        emp.department_id = data["department_id"]

    if "job_position_id" in data:
        require_active(db, JobPosition, data["job_position_id"], "Job position")
        emp.job_position_id = data["job_position_id"]

    if "working_schedule_id" in data:
        validate_schedule_assignable(db, data["working_schedule_id"])
        emp.working_schedule_id = data["working_schedule_id"]

    if "manager_id" in data:
        new_manager_id = data["manager_id"]
        if new_manager_id == emp.id:
            raise ValidationException(
                "An employee cannot be their own manager."
            )
        if new_manager_id is not None:
            get_or_404(db, Employee, new_manager_id, "Manager")
            if would_create_cycle(
                db, Employee, emp.id, "manager_id", new_manager_id
            ):
                raise ValidationException(
                    "This change would create a cycle in the management chain."
                )
        emp.manager_id = new_manager_id

    for field in (
        "full_name", "phone", "employee_type", "status",
        "date_of_joining", "work_location", "company_id",
    ):
        if field in data:
            setattr(emp, field, data[field])

    db.commit()
    db.refresh(emp)
    return _build_detail(db, emp)


def soft_delete_employee(db: Session, employee_id: int) -> None:
    """DELETE /employees/{id} — soft delete via status='inactive'."""
    emp = get_or_404(db, Employee, employee_id, "Employee")
    emp.status = EmployeeStatus.inactive
    db.commit()


def terminate_employee(db: Session, employee_id: int) -> dict:
    """POST /employees/{id}/terminate — explicit, auditable status change."""
    emp = get_or_404(db, Employee, employee_id, "Employee")
    if emp.status == EmployeeStatus.terminated:
        raise ConflictException(
            f"Employee '{emp.full_name}' is already terminated."
        )
    emp.status = EmployeeStatus.terminated
    db.commit()
    db.refresh(emp)
    return _build_detail(db, emp)