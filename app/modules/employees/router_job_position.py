"""Job Position endpoints (OWNER: Ameen). RBAC: HR roles only."""

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.dependencies import require_roles
from app.models.auth import User
from app.schemas.employee import (
    JobPositionCreate,
    JobPositionRead,
    JobPositionUpdate,
    Paginated,
)

from . import service_job_position
from .service import HR_ROLES

router = APIRouter()

HR_ACCESS = require_roles(*HR_ROLES)


@router.get("/job-positions", response_model=Paginated[JobPositionRead])
def list_job_positions(
    page: int | None = Query(default=None, ge=1),
    page_size: int | None = Query(default=None, ge=1, le=200),
    search: str | None = None,
    department_id: int | None = None,
    is_active: bool | None = None,
    _: User = Depends(HR_ACCESS),
    db: Session = Depends(get_db),
) -> dict:
    rows, total, page, page_size = service_job_position.list_job_positions(
        db, page, page_size, search, department_id, is_active
    )
    return {"items": rows, "total": total, "page": page, "page_size": page_size}


@router.get(
    "/job-positions/{job_position_id}", response_model=JobPositionRead
)
def get_job_position(
    job_position_id: int,
    _: User = Depends(HR_ACCESS),
    db: Session = Depends(get_db),
) -> JobPositionRead:
    return service_job_position.get_job_position(db, job_position_id)


@router.post(
    "/job-positions",
    response_model=JobPositionRead,
    status_code=status.HTTP_201_CREATED,
)
def create_job_position(
    payload: JobPositionCreate,
    _: User = Depends(HR_ACCESS),
    db: Session = Depends(get_db),
) -> JobPositionRead:
    return service_job_position.create_job_position(db, payload)


@router.patch(
    "/job-positions/{job_position_id}", response_model=JobPositionRead
)
def update_job_position(
    job_position_id: int,
    payload: JobPositionUpdate,
    _: User = Depends(HR_ACCESS),
    db: Session = Depends(get_db),
) -> JobPositionRead:
    return service_job_position.update_job_position(db, job_position_id, payload)


@router.delete(
    "/job-positions/{job_position_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_job_position(
    job_position_id: int,
    _: User = Depends(HR_ACCESS),
    db: Session = Depends(get_db),
) -> None:
    """Soft delete; 409 with counts if active employees remain assigned."""
    service_job_position.soft_delete_job_position(db, job_position_id)