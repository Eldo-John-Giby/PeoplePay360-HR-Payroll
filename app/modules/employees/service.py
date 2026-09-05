"""Shared helpers for the Employee module service layer (OWNER: Ameen).

Entity services (`service_department.py`, `service_job_position.py`,
`service_schedule.py`, `service_employee.py`, `service_contract.py`) import
from here. This module holds ONLY cross-cutting, pure helpers so it never
imports the entity services (no circular imports):

- pagination clamping (arch doc §5.5)
- the pure `total_weekly_hours` function (unit-testable without a DB)
- weekly-pattern-line validation (overlap / end>start / day range)
- get-or-404 / require-active referential checks (§5.3)
- hierarchy cycle detection (departments, management chain)
- RBAC helpers for the module's HR roles
"""

from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundException, ValidationException
from app.models.auth import User

# Roles allowed full CRUD on Employees/Departments/Job Positions/Working
# Schedules/Contracts (architecture doc §4.7). EMPLOYEE is deliberately absent.
HR_ROLES = frozenset(
    {"HR_MANAGER", "HR_PAYROLL_USER", "HR_PAYROLL_MANAGER", "ADMIN"}
)

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 200
MAX_HIERARCHY_DEPTH = 20  # guard against infinite loops on corrupt data


# ---------------------------------------------------------------------------
# RBAC
# ---------------------------------------------------------------------------

def has_hr_access(user: User) -> bool:
    """True if the user holds any role allowed full CRUD in this module."""
    return any(role.name in HR_ROLES for role in user.roles)


# ---------------------------------------------------------------------------
# Pagination (arch doc §4.4 / §5.5)
# ---------------------------------------------------------------------------

def paginate(page: int | None, page_size: int | None) -> tuple[int, int]:
    """Clamp pagination: page >= 1, 1 <= page_size <= 200."""
    page = page if page is not None and page > 0 else 1
    if page_size is None or page_size < 1:
        page_size = DEFAULT_PAGE_SIZE
    page_size = min(page_size, MAX_PAGE_SIZE)
    return page, page_size


# ---------------------------------------------------------------------------
# Working schedule weekly hours (spec §2.3 — pure function, no DB)
# ---------------------------------------------------------------------------

def compute_total_weekly_hours(lines: list[Any]) -> Decimal:
    """sum((end_time - start_time) - break_minutes) across all lines, in hours.

    Pure function (no DB access) so it is unit-testable in isolation.
    """
    total_minutes = sum(
        (line.end_time.hour * 60 + line.end_time.minute)
        - (line.start_time.hour * 60 + line.start_time.minute)
        - (line.break_minutes or 0)
        for line in lines
    )
    return (Decimal(total_minutes) / Decimal(60)).quantize(Decimal("0.01"))


# ---------------------------------------------------------------------------
# Weekly pattern line validation (spec §2.3 edge cases)
# ---------------------------------------------------------------------------

def validate_schedule_lines(lines: list[Any]) -> None:
    """Validate a full set of weekly pattern lines; raise 422 on violations.

    - day_of_week outside 0..6            -> 422 (DB CHECK backs this up)
    - end_time <= start_time              -> 422 (DB CHECK backs this up)
    - break longer than the shift         -> 422 (would yield negative hours)
    - two lines same day, overlapping     -> 422 (DB does NOT enforce this)
    """
    by_day: dict[int, list[tuple]] = {}
    for line in lines:
        if not (0 <= line.day_of_week <= 6):
            raise ValidationException(
                "day_of_week must be between 0 (Monday) and 6 (Sunday)."
            )
        if line.end_time <= line.start_time:
            raise ValidationException(
                "end_time must be after start_time on every line."
            )
        duration_min = (
            line.end_time.hour * 60 + line.end_time.minute
        ) - (line.start_time.hour * 60 + line.start_time.minute)
        if line.break_minutes >= duration_min:
            raise ValidationException(
                "break_minutes must be shorter than the shift duration."
            )
        by_day.setdefault(line.day_of_week, []).append(
            (line.start_time, line.end_time)
        )

    for day, ranges in by_day.items():
        ordered = sorted(ranges)
        for (prev_start, prev_end), (start, end) in zip(ordered, ordered[1:]):
            if start < prev_end:
                raise ValidationException(
                    f"Overlapping time ranges on day_of_week={day}: "
                    f"{prev_start}-{prev_end} overlaps {start}-{end}."
                )


# ---------------------------------------------------------------------------
# Referential existence / active checks (arch doc §5.3)
# ---------------------------------------------------------------------------

def get_or_404(db: Session, model: Any, obj_id: int, label: str) -> Any:
    """Fetch by PK or raise 404 — never leak a raw IntegrityError."""
    obj = db.get(model, obj_id)
    if obj is None:
        raise NotFoundException(f"{label} {obj_id} not found.")
    return obj


def require_active(db: Session, model: Any, obj_id: int, label: str) -> Any:
    """Fetch by PK, then require is_active — else 422 (spec §2.4 schedule)."""
    obj = get_or_404(db, model, obj_id, label)
    if not obj.is_active:
        raise ValidationException(f"{label} {obj_id} is inactive.")
    return obj


def count_rows(db: Session, model: Any, *whereclauses) -> int:
    stmt = select(model.id)
    for wc in whereclauses:
        stmt = stmt.where(wc)
    return len(db.scalars(stmt).all())


# ---------------------------------------------------------------------------
# Hierarchy cycle detection (departments / management chain)
# ---------------------------------------------------------------------------

def would_create_cycle(
    db: Session,
    model: Any,
    obj_id: int,
    parent_field: str,
    new_parent_id: int | None,
) -> bool:
    """True if setting `new_parent_id` as `obj_id`'s parent creates a cycle.

    Walks the parent chain from `new_parent_id` up (capped at
    MAX_HIERARCHY_DEPTH to survive corrupt data) and returns True as soon as
    it reaches `obj_id`. Raises 422 if the chain is deeper than the cap.
    """
    if new_parent_id is None:
        return False
    current = new_parent_id
    for _ in range(MAX_HIERARCHY_DEPTH):
        if current == obj_id:
            return True
        node = db.get(model, current)
        if node is None:
            return False
        current = getattr(node, parent_field)
        if current is None:
            return False
    raise ValidationException(
        f"{model.__name__} hierarchy exceeds {MAX_HIERARCHY_DEPTH} levels — "
        "refusing to walk further (possible corrupt parent chain)."
    )