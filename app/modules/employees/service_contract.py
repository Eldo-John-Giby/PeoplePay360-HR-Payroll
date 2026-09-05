"""Contract service layer (OWNER: Ameen).

Business rules (spec §2.5):
- POST always creates a `draft` — the ONLY way to become `running` is
  POST /contracts/{id}/activate (a raw status PATCH is impossible).
- `activate` runs in ONE transaction: expires the employee's current
  running contract (end_date = target.start_date - 1 day, no gap/overlap),
  then marks the target running. Out-of-order activation (running contract
  starts AFTER the target) -> 422. The partial unique index
  `uq_contracts_one_running_per_employee` is the DB backstop; a concurrent
  race surfaces as IntegrityError/StaleDataError -> translated to a clean 409.
- PATCH: draft only (editing running/expired/cancelled -> 409). The correct
  flow for a wage change is: new draft + activate (history preserved).
- Optimistic locking via `version_id`: stale client version -> 409.
- Contracts snapshot department/job_position/schedule/structure at creation;
  later changes to the Employee NEVER touch past contracts.
- No hard DELETE: only `cancel` (draft) / `expire` (running) exist.
- "Currently applicable contract" resolver for Steve's payroll engine:
  status='running' means "current/next contract in force"; the real
  eligibility filter is the date range
  `start_date <= period_date <= (end_date or infinity)`.
"""

from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.core.exceptions import ConflictException, ValidationException
from app.models.employee import Contract, Employee
from app.models.enums import ContractStatus, EmployeeStatus
from app.models.organization import (
    Department,
    JobPosition,
    WorkingSchedule,
    WorkingScheduleLine,
)
from app.models.payroll import SalaryStructure
from app.schemas.employee import (
    ContractActionRequest,
    ContractCreate,
    ContractUpdate,
)

from .service import (
    compute_total_weekly_hours,
    get_or_404,
    paginate,
    require_active,
)
from .service_schedule import validate_schedule_assignable


# ---------------------------------------------------------------------------
# Serialization helpers (batch-friendly — no N+1 on list endpoints)
# ---------------------------------------------------------------------------

def _read_dict(
    contract: Contract,
    employee: Employee | None,
    dept: Department | None,
    pos: JobPosition | None,
    sched: WorkingSchedule | None,
    sched_lines: list,
    structure: SalaryStructure | None,
) -> dict:
    return {
        "id": contract.id,
        "contract_number": contract.contract_number,
        "employee": (
            {
                "id": employee.id,
                "full_name": employee.full_name,
                "work_email": employee.work_email,
                "status": employee.status,
            }
            if employee else None
        ),
        "department": (
            {"id": dept.id, "name": dept.name, "is_active": dept.is_active}
            if dept else None
        ),
        "job_position": (
            {"id": pos.id, "title": pos.title, "is_active": pos.is_active}
            if pos else None
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
        "salary_structure": (
            {"id": structure.id, "name": structure.name, "code": structure.code}
            if structure else None
        ),
        "wage_monthly": contract.wage_monthly,
        "start_date": contract.start_date,
        "end_date": contract.end_date,
        "status": contract.status,
        "version_id": contract.version_id,
        "created_at": contract.created_at,
        "updated_at": contract.updated_at,
    }


def _read_dicts(db: Session, contracts: list[Contract]) -> list[dict]:
    """Serialize a list of contracts with batched FK lookups (5 queries per
    page, not 5 per row)."""
    if not contracts:
        return []
    emp_ids = {c.employee_id for c in contracts}
    dept_ids = {c.department_id for c in contracts}
    pos_ids = {c.job_position_id for c in contracts}
    sched_ids = {c.working_schedule_id for c in contracts}
    struct_ids = {c.salary_structure_id for c in contracts}

    employees = {
        e.id: e for e in db.scalars(
            select(Employee).where(Employee.id.in_(emp_ids))
        )
    }
    departments = {
        d.id: d for d in db.scalars(
            select(Department).where(Department.id.in_(dept_ids))
        )
    }
    positions = {
        p.id: p for p in db.scalars(
            select(JobPosition).where(JobPosition.id.in_(pos_ids))
        )
    }
    structures = {
        s.id: s for s in db.scalars(
            select(SalaryStructure).where(SalaryStructure.id.in_(struct_ids))
        )
    }
    schedules = {
        s.id: s for s in db.scalars(
            select(WorkingSchedule).where(WorkingSchedule.id.in_(sched_ids))
        )
    }
    lines_by_schedule: dict[int, list[WorkingScheduleLine]] = defaultdict(list)
    if sched_ids:
        for line in db.scalars(
            select(WorkingScheduleLine).where(
                WorkingScheduleLine.working_schedule_id.in_(sched_ids)
            )
        ):
            lines_by_schedule[line.working_schedule_id].append(line)

    return [
        _read_dict(
            c,
            employees.get(c.employee_id),
            departments.get(c.department_id),
            positions.get(c.job_position_id),
            schedules.get(c.working_schedule_id),
            lines_by_schedule.get(c.working_schedule_id, []),
            structures.get(c.salary_structure_id),
        )
        for c in contracts
    ]


# ---------------------------------------------------------------------------
# Shared write guards
# ---------------------------------------------------------------------------

def _next_contract_number(db: Session, start_date: date) -> str:
    seq = (db.scalar(select(func.max(Contract.id))) or 0) + 1
    return f"CON/{start_date.year}/{seq:04d}"


def _check_version(contract: Contract, client_version: int | None) -> None:
    """Optimistic lock (arch doc §5.1): stale client version -> 409."""
    if client_version is not None and client_version != contract.version_id:
        raise ConflictException(
            "This contract was modified by someone else. "
            "Please refresh and try again."
        )


def _validate_contract_fks(
    db: Session, payload: ContractCreate | ContractUpdate
) -> None:
    """Every FK in the body must exist and be active (arch doc §5.3)."""
    data = payload.model_dump(exclude_unset=True)
    if "department_id" in data:
        require_active(db, Department, data["department_id"], "Department")
    if "job_position_id" in data:
        require_active(db, JobPosition, data["job_position_id"], "Job position")
    if "working_schedule_id" in data:
        validate_schedule_assignable(db, data["working_schedule_id"])
    if "salary_structure_id" in data:
        require_active(db, SalaryStructure, data["salary_structure_id"], "Salary structure")


def _commit_or_conflict(db: Session) -> None:
    """Commit, translating optimistic-lock / unique-index races to clean 409s
    (belt-and-suspenders on top of the transaction logic)."""
    try:
        db.commit()
    except (StaleDataError, IntegrityError):
        db.rollback()
        raise ConflictException(
            "This contract was modified concurrently by someone else. "
            "Please refresh and try again."
        )


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

def list_contracts(
    db: Session,
    page: int | None,
    page_size: int | None,
    employee_id: int | None,
    status: str | None,
    department_id: int | None,
) -> tuple[list[dict], int, int, int]:
    page, page_size = paginate(page, page_size)
    stmt = select(Contract)
    if employee_id is not None:
        stmt = stmt.where(Contract.employee_id == employee_id)
    if status is not None:
        stmt = stmt.where(Contract.status == status)
    if department_id is not None:
        stmt = stmt.where(Contract.department_id == department_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(Contract.start_date.desc(), Contract.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return _read_dicts(db, list(rows)), total, page, page_size


def get_contract(db: Session, contract_id: int) -> dict:
    contract = get_or_404(db, Contract, contract_id, "Contract")
    return _read_dicts(db, [contract])[0]


def get_contracts_for_employee(
    db: Session, employee_id: int, status: str | None
) -> list[dict]:
    """Chronological history for one employee (spec: "clearly highlighting
    the active contract" — the running contract is in the list with
    status='running'; frontend highlights it)."""
    stmt = (
        select(Contract)
        .where(Contract.employee_id == employee_id)
        .order_by(Contract.start_date.desc(), Contract.id.desc())
    )
    if status is not None:
        stmt = stmt.where(Contract.status == status)
    return _read_dicts(db, list(db.scalars(stmt)))


def get_applicable_contract(
    db: Session, employee_id: int, period_date: date
) -> Contract | None:
    """THE resolver for Steve's payroll engine.

    status='running' means "the current/next contract in force"; the actual
    eligibility filter is the date range. A pre-scheduled (future start)
    running contract is NOT applicable to an earlier period.
    """
    return db.scalar(
        select(Contract).where(
            Contract.employee_id == employee_id,
            Contract.status == ContractStatus.running,
            Contract.start_date <= period_date,
            or_(
                Contract.end_date.is_(None),
                Contract.end_date >= period_date,
            ),
        )
    )


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------

def create_contract(db: Session, payload: ContractCreate) -> dict:
    employee = get_or_404(db, Employee, payload.employee_id, "Employee")
    if employee.status != EmployeeStatus.active:
        raise ValidationException(
            "Cannot create a contract for an employee with status "
            f"'{employee.status.value}' — only active employees get new "
            "contracts (backfill historical records via an Admin flag)."
        )
    _validate_contract_fks(db, payload)
    if payload.end_date is not None and payload.end_date < payload.start_date:
        raise ValidationException("end_date must be on or after start_date.")

    contract = Contract(
        contract_number=_next_contract_number(db, payload.start_date),
        status=ContractStatus.draft,  # NEVER directly running
        **payload.model_dump(),
    )
    db.add(contract)
    _commit_or_conflict(db)
    db.refresh(contract)
    return _read_dicts(db, [contract])[0]


def update_contract(
    db: Session, contract_id: int, payload: ContractUpdate
) -> dict:
    contract = get_or_404(db, Contract, contract_id, "Contract")
    _check_version(contract, payload.version_id)
    if contract.status != ContractStatus.draft:
        raise ConflictException(
            f"Only draft contracts can be edited (this one is "
            f"'{contract.status.value}'). To change terms, create a new "
            "draft contract with the new wage and activate it — history is "
            "preserved that way."
        )
    _validate_contract_fks(db, payload)

    data = payload.model_dump(exclude_unset=True)
    new_start = data.get("start_date", contract.start_date)
    new_end = data.get("end_date", contract.end_date)
    if new_end is not None and new_end < new_start:
        raise ValidationException("end_date must be on or after start_date.")

    for field, value in data.items():
        if field == "version_id":
            continue
        setattr(contract, field, value)
    _commit_or_conflict(db)
    db.refresh(contract)
    return _read_dicts(db, [contract])[0]


def activate_contract(
    db: Session, contract_id: int, payload: ContractActionRequest
) -> dict:
    """The ONLY way a contract becomes `running` (spec §2.5) — one transaction:

    1. target must be `draft` (else 409)
    2. find the employee's current `running` contract, if any
    3. if present:
       - reject 422 if the running contract starts ON/AFTER the target
         (activating out of chronological order isn't allowed)
       - expire it with end_date = target.start_date - 1 day (no gap/overlap)
    4. mark target `running`; commit atomically.
    """
    contract = get_or_404(db, Contract, contract_id, "Contract")
    _check_version(contract, payload.version_id)
    if contract.status != ContractStatus.draft:
        raise ConflictException(
            f"Cannot activate a contract with status "
            f"'{contract.status.value}' — only draft contracts can be "
            "activated."
        )

    running = db.scalar(
        select(Contract).where(
            Contract.employee_id == contract.employee_id,
            Contract.status == ContractStatus.running,
        )
    )

    if running is not None:
        if running.start_date >= contract.start_date:
            raise ValidationException(
                f"Cannot activate a contract starting {contract.start_date}: "
                f"the employee's running contract starts {running.start_date}. "
                "Activate in chronological order — pick a later start date."
            )
        # Close the gap without ever shortening/extending an existing
        # end_date beyond what's needed (keeps ck_contracts_end_after_start).
        new_end = contract.start_date - timedelta(days=1)
        if running.end_date is None or running.end_date > new_end:
            running.end_date = new_end
        running.status = ContractStatus.expired

    contract.status = ContractStatus.running
    _commit_or_conflict(db)
    db.refresh(contract)
    return _read_dicts(db, [contract])[0]


def expire_contract(
    db: Session, contract_id: int, payload: ContractActionRequest
) -> dict:
    contract = get_or_404(db, Contract, contract_id, "Contract")
    _check_version(contract, payload.version_id)
    if contract.status != ContractStatus.running:
        raise ConflictException(
            f"Only running contracts can be expired (this one is "
            f"'{contract.status.value}')."
        )
    # end_date=today if not already set — but never before start_date
    # (a future-dated running contract keeps end_date NULL; it never started).
    if contract.end_date is None and contract.start_date <= date.today():
        contract.end_date = date.today()
    contract.status = ContractStatus.expired
    _commit_or_conflict(db)
    db.refresh(contract)
    return _read_dicts(db, [contract])[0]


def cancel_contract(
    db: Session, contract_id: int, payload: ContractActionRequest
) -> dict:
    """For draft contracts only (spec §2.5) — preserves history."""
    contract = get_or_404(db, Contract, contract_id, "Contract")
    _check_version(contract, payload.version_id)
    if contract.status != ContractStatus.draft:
        raise ConflictException(
            f"Only draft contracts can be cancelled (this one is "
            f"'{contract.status.value}')."
        )
    contract.status = ContractStatus.cancelled
    _commit_or_conflict(db)
    db.refresh(contract)
    return _read_dicts(db, [contract])[0]