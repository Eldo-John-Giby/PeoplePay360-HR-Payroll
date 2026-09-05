"""Department endpoints (OWNER: Ameen). Router stays thin (arch doc §4.3):
parse request -> call service -> return. RBAC: HR roles only."""

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.dependencies import require_roles
from app.models.auth import User
from app.schemas.employee import (
    DepartmentCreate,
    DepartmentRead,
    DepartmentUpdate,
    Paginated,
)

from . import service_department
from .service import HR_ROLES

router = APIRouter()

HR_ACCESS = require_roles(*HR_ROLES)


@router.get("/departments", response_model=Paginated[DepartmentRead])
def list_departments(
    page: int | None = Query(default=None, ge=1),
    page_size: int | None = Query(default=None, ge=1, le=200),
    search: str | None = None,
    parent_id: int | None = None,
    is_active: bool | None = None,
    _: User = Depends(HR_ACCESS),
    db: Session = Depends(get_db),
) -> dict:
    rows, total, page, page_size = service_department.list_departments(
        db, page, page_size, search, parent_id, is_active
    )
    return {"items": rows, "total": total, "page": page, "page_size": page_size}


@router.get(
    "/departments/{department_id}", response_model=DepartmentRead
)
def get_department(
    department_id: int,
    _: User = Depends(HR_ACCESS),
    db: Session = Depends(get_db),
) -> DepartmentRead:
    return service_department.get_department(db, department_id)


@router.post(
    "/departments",
    response_model=DepartmentRead,
    status_code=status.HTTP_201_CREATED,
)
def create_department(
    payload: DepartmentCreate,
    _: User = Depends(HR_ACCESS),
    db: Session = Depends(get_db),
) -> DepartmentRead:
    return service_department.create_department(db, payload)


@router.patch(
    "/departments/{department_id}", response_model=DepartmentRead
)
def update_department(
    department_id: int,
    payload: DepartmentUpdate,
    _: User = Depends(HR_ACCESS),
    db: Session = Depends(get_db),
) -> DepartmentRead:
    return service_department.update_department(db, department_id, payload)


@router.delete(
    "/departments/{department_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_department(
    department_id: int,
    _: User = Depends(HR_ACCESS),
    db: Session = Depends(get_db),
) -> None:
    """Soft delete (is_active=false); 409 with counts if still in use."""
    service_department.soft_delete_department(db, department_id)