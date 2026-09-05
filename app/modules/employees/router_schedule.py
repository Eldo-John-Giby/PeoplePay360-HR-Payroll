"""Working Schedule endpoints (OWNER: Ameen). RBAC: HR roles only."""

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.dependencies import require_roles
from app.models.auth import User
from app.schemas.employee import (
    Paginated,
    WorkingScheduleCreate,
    WorkingScheduleLineCreate,
    WorkingScheduleListItem,
    WorkingScheduleRead,
    WorkingScheduleUpdate,
)

from . import service_schedule
from .service import HR_ROLES

router = APIRouter()

HR_ACCESS = require_roles(*HR_ROLES)


@router.get(
    "/working-schedules", response_model=Paginated[WorkingScheduleListItem]
)
def list_working_schedules(
    page: int | None = Query(default=None, ge=1),
    page_size: int | None = Query(default=None, ge=1, le=200),
    search: str | None = None,
    is_active: bool | None = None,
    _: User = Depends(HR_ACCESS),
    db: Session = Depends(get_db),
) -> dict:
    """List view includes the computed `total_weekly_hours` per schedule."""
    rows, total, page, page_size = service_schedule.list_working_schedules(
        db, page, page_size, search, is_active
    )
    return {"items": rows, "total": total, "page": page, "page_size": page_size}


@router.get(
    "/working-schedules/{schedule_id}", response_model=WorkingScheduleRead
)
def get_working_schedule(
    schedule_id: int,
    _: User = Depends(HR_ACCESS),
    db: Session = Depends(get_db),
) -> WorkingScheduleRead:
    return service_schedule.get_working_schedule(db, schedule_id)


@router.post(
    "/working-schedules",
    response_model=WorkingScheduleRead,
    status_code=status.HTTP_201_CREATED,
)
def create_working_schedule(
    payload: WorkingScheduleCreate,
    _: User = Depends(HR_ACCESS),
    db: Session = Depends(get_db),
) -> WorkingScheduleRead:
    """Creates the schedule + its weekly pattern lines atomically."""
    return service_schedule.create_working_schedule(db, payload)


@router.patch(
    "/working-schedules/{schedule_id}", response_model=WorkingScheduleRead
)
def update_working_schedule(
    schedule_id: int,
    payload: WorkingScheduleUpdate,
    _: User = Depends(HR_ACCESS),
    db: Session = Depends(get_db),
) -> WorkingScheduleRead:
    return service_schedule.update_working_schedule(db, schedule_id, payload)


@router.put(
    "/working-schedules/{schedule_id}/lines",
    response_model=WorkingScheduleRead,
)
def replace_schedule_lines(
    schedule_id: int,
    lines: list[WorkingScheduleLineCreate],
    _: User = Depends(HR_ACCESS),
    db: Session = Depends(get_db),
) -> WorkingScheduleRead:
    """REPLACES the full set of weekly lines in one transaction."""
    return service_schedule.replace_schedule_lines(db, schedule_id, lines)


@router.delete(
    "/working-schedules/{schedule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_working_schedule(
    schedule_id: int,
    _: User = Depends(HR_ACCESS),
    db: Session = Depends(get_db),
) -> None:
    """Soft delete; 409 with counts if referenced by active employees/contracts."""
    service_schedule.soft_delete_working_schedule(db, schedule_id)