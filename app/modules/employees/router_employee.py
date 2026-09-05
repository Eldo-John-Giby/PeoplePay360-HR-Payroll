"""Employee endpoints (OWNER: Ameen).

Route ordering matters: `/employees/me` and `/employees/me/contracts` MUST be
declared before `/employees/{employee_id}` or FastAPI would try to parse
"me" as an int path parameter (422 instead of the intended route).

RBAC (arch doc §4.7):
- EMPLOYEE: GET /employees/me, /employees/me/contracts, and own
  /employees/{id} reads (403 for anyone else's). No writes.
- HR roles: full CRUD.
"""

from typing import Union

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.dependencies import get_current_user, require_roles
from app.models.auth import User
from app.schemas.employee import (
    ContractRead,
    EmployeeCreate,
    EmployeeDetail,
    EmployeeListItem,
    EmployeeUpdate,
    GroupedList,
    Paginated,
    RelatedSummary,
)

from . import service_employee
from .service import HR_ROLES

router = APIRouter()

HR_ACCESS = require_roles(*HR_ROLES)


# ---------------------------------------------------------------------------
# Self-service (any authenticated user)
# ---------------------------------------------------------------------------

@router.get("/employees/me", response_model=EmployeeDetail)
def get_my_employee(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """The logged-in user's own employee record. 404 if no linked employee."""
    return service_employee.get_my_employee(db, current_user)


@router.get("/employees/me/contracts", response_model=list[ContractRead])
def get_my_contracts(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    """EMPLOYEE self-service: own contract history."""
    return service_employee.list_employee_contracts(
        db, current_user.employee_id, None, current_user
    )


# ---------------------------------------------------------------------------
# HR CRUD
# ---------------------------------------------------------------------------

@router.get(
    "/employees",
    response_model=Union[Paginated[EmployeeListItem], GroupedList[EmployeeListItem]],
)
def list_employees(
    page: int | None = Query(default=None, ge=1),
    page_size: int | None = Query(default=None, ge=1, le=200),
    department_id: int | None = None,
    status: str | None = None,
    employee_type: str | None = None,
    manager_id: int | None = None,
    search: str | None = None,
    group_by: str | None = Query(
        default=None, pattern="^(status|department)$",
        description="Kanban: group the filtered board by status or department",
    ),
    sort_by: str | None = None,
    sort_dir: str | None = Query(default=None, pattern="^(asc|desc)$"),
    _: User = Depends(HR_ACCESS),
    db: Session = Depends(get_db),
) -> dict:
    """List view + Kanban board (?group_by=status|department) from one endpoint.

    Kanban returns the FULL filtered set grouped, so each column's `count` is
    exact (a board needs all columns, not one page).
    """
    items, total, page, page_size, grouped = service_employee.list_employees(
        db, page, page_size, department_id, status, employee_type,
        manager_id, search, group_by, sort_by, sort_dir,
    )
    if grouped:
        return {"groups": items, "total": total, "page": page, "page_size": page_size}
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.post(
    "/employees",
    response_model=EmployeeDetail,
    status_code=status.HTTP_201_CREATED,
)
def create_employee(
    payload: EmployeeCreate,
    _: User = Depends(HR_ACCESS),
    db: Session = Depends(get_db),
) -> dict:
    """Creates the Employee row only — contracts are created separately."""
    return service_employee.create_employee(db, payload)


@router.get("/employees/{employee_id}", response_model=EmployeeDetail)
def get_employee(
    employee_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Full Form payload + smart-button counts. EMPLOYEE role: own record only."""
    return service_employee.get_employee(db, employee_id, current_user)


@router.patch("/employees/{employee_id}", response_model=EmployeeDetail)
def update_employee(
    employee_id: int,
    payload: EmployeeUpdate,
    _: User = Depends(HR_ACCESS),
    db: Session = Depends(get_db),
) -> dict:
    return service_employee.update_employee(db, employee_id, payload)


@router.delete(
    "/employees/{employee_id}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_employee(
    employee_id: int,
    _: User = Depends(HR_ACCESS),
    db: Session = Depends(get_db),
) -> None:
    """Soft delete: status -> 'inactive'."""
    service_employee.soft_delete_employee(db, employee_id)


@router.post(
    "/employees/{employee_id}/terminate", response_model=EmployeeDetail
)
def terminate_employee(
    employee_id: int,
    _: User = Depends(HR_ACCESS),
    db: Session = Depends(get_db),
) -> dict:
    """Explicit, auditable status change to 'terminated'."""
    return service_employee.terminate_employee(db, employee_id)


@router.get(
    "/employees/{employee_id}/related-summary", response_model=RelatedSummary
)
def get_related_summary(
    employee_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Smart-button badge counts (wireframe). EMPLOYEE role: own record only."""
    return service_employee.related_summary_for(db, employee_id, current_user)


@router.get(
    "/employees/{employee_id}/contracts", response_model=list[ContractRead]
)
def get_employee_contracts(
    employee_id: int,
    status: str | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    """Smart-button filtered contracts for one employee."""
    return service_employee.list_employee_contracts(
        db, employee_id, status, current_user
    )