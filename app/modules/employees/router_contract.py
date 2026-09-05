"""Contract endpoints (OWNER: Ameen). RBAC: HR roles only.

History is preserved by design: there is NO DELETE endpoint — only `cancel`
(draft) and `expire` (running) transitions. Wage changes go through
create-new-draft + activate, never a PATCH on a running contract.
"""

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.dependencies import require_roles
from app.models.auth import User
from app.schemas.employee import (
    ContractActionRequest,
    ContractCreate,
    ContractRead,
    ContractUpdate,
    Paginated,
)

from . import service_contract
from .service import HR_ROLES

router = APIRouter()

HR_ACCESS = require_roles(*HR_ROLES)


@router.get("/contracts", response_model=Paginated[ContractRead])
def list_contracts(
    page: int | None = Query(default=None, ge=1),
    page_size: int | None = Query(default=None, ge=1, le=200),
    employee_id: int | None = None,
    status: str | None = None,
    department_id: int | None = None,
    _: User = Depends(HR_ACCESS),
    db: Session = Depends(get_db),
) -> dict:
    """Chronological contract history — filter by employee/status/department."""
    rows, total, page, page_size = service_contract.list_contracts(
        db, page, page_size, employee_id, status, department_id
    )
    return {"items": rows, "total": total, "page": page, "page_size": page_size}


@router.get("/contracts/{contract_id}", response_model=ContractRead)
def get_contract(
    contract_id: int,
    _: User = Depends(HR_ACCESS),
    db: Session = Depends(get_db),
) -> dict:
    return service_contract.get_contract(db, contract_id)


@router.post(
    "/contracts",
    response_model=ContractRead,
    status_code=status.HTTP_201_CREATED,
)
def create_contract(
    payload: ContractCreate,
    _: User = Depends(HR_ACCESS),
    db: Session = Depends(get_db),
) -> dict:
    """Always creates a `draft` — the only way to become `running` is the
    Activate action."""
    return service_contract.create_contract(db, payload)


@router.patch("/contracts/{contract_id}", response_model=ContractRead)
def update_contract(
    contract_id: int,
    payload: ContractUpdate,
    _: User = Depends(HR_ACCESS),
    db: Session = Depends(get_db),
) -> dict:
    """Edit while `draft` only (running/expired/cancelled -> 409). Send the
    `version_id` you hold for optimistic locking."""
    return service_contract.update_contract(db, contract_id, payload)


@router.post("/contracts/{contract_id}/activate", response_model=ContractRead)
def activate_contract(
    contract_id: int,
    payload: ContractActionRequest,
    _: User = Depends(HR_ACCESS),
    db: Session = Depends(get_db),
) -> dict:
    """THE only way a contract becomes `running`. Expires the employee's
    current running contract (no date gap/overlap) in the same transaction."""
    return service_contract.activate_contract(db, contract_id, payload)


@router.post("/contracts/{contract_id}/expire", response_model=ContractRead)
def expire_contract(
    contract_id: int,
    payload: ContractActionRequest,
    _: User = Depends(HR_ACCESS),
    db: Session = Depends(get_db),
) -> dict:
    """Running -> expired; sets end_date=today if not already set."""
    return service_contract.expire_contract(db, contract_id, payload)


@router.post("/contracts/{contract_id}/cancel", response_model=ContractRead)
def cancel_contract(
    contract_id: int,
    payload: ContractActionRequest,
    _: User = Depends(HR_ACCESS),
    db: Session = Depends(get_db),
) -> dict:
    """Draft -> cancelled (history preserved; no hard delete anywhere)."""
    return service_contract.cancel_contract(db, contract_id, payload)