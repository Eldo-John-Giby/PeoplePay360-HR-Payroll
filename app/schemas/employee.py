"""Pydantic v2 request/response models for the Employee module (Ameen's slice).

Entities: Department, JobPosition, WorkingSchedule (+Lines), Employee, Contract.

Conventions (architecture doc §3 / §4):
- Separate *Create / *Update / *Read schemas per entity — never reuse one
  schema for both directions.
- *Update schemas are all-optional (PATCH semantics); use `model_fields_set`
  in the service to tell "absent" from "explicitly null".
- *Read schemas use `from_attributes` where the ORM object itself is returned;
  Employee/Contract reads are assembled as dicts in the service (those models
  have no relationship attrs for department/job/schedule), so they are plain
  models validated by FastAPI against the returned dict.
- Money is condecimal(ge=0, max_digits=12, decimal_places=2) (arch doc §4.9).
"""

from datetime import date, datetime, time
from decimal import Decimal
from typing import Annotated, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.enums import (
    ContractStatus,
    EmployeeStatus,
    EmployeeType,
    ScheduleType,
)

T = TypeVar("T")

# ---------------------------------------------------------------------------
# Shared / envelope
# ---------------------------------------------------------------------------

WageDecimal = Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=2)]


class Paginated(BaseModel, Generic[T]):
    """Standard pagination envelope (architecture doc §4.4)."""

    items: list[T]
    total: int
    page: int
    page_size: int


class Group(BaseModel, Generic[T]):
    """One Kanban column: key = status or department name."""

    key: str
    count: int
    items: list[T]


class GroupedList(BaseModel, Generic[T]):
    """Kanban variant of the list endpoint (?group_by=status|department)."""

    groups: list[Group[T]]
    total: int
    page: int
    page_size: int


def _from_orm() -> ConfigDict:
    return ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Department
# ---------------------------------------------------------------------------

class DepartmentSummary(BaseModel):
    model_config = _from_orm()

    id: int
    name: str
    is_active: bool = True


class DepartmentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    parent_department_id: int | None = None
    company_id: int | None = None
    is_active: bool = True


class DepartmentUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    parent_department_id: int | None = None
    company_id: int | None = None
    is_active: bool | None = None


class DepartmentRead(BaseModel):
    model_config = _from_orm()

    id: int
    name: str
    parent_department_id: int | None = None
    company_id: int | None = None
    is_active: bool
    created_at: datetime
    updated_at: datetime
    parent: DepartmentSummary | None = None


# ---------------------------------------------------------------------------
# Job Position
# ---------------------------------------------------------------------------

class JobPositionSummary(BaseModel):
    model_config = _from_orm()

    id: int
    title: str
    is_active: bool = True


class JobPositionCreate(BaseModel):
    title: str = Field(min_length=1, max_length=100)
    department_id: int
    is_active: bool = True


class JobPositionUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=100)
    department_id: int | None = None
    is_active: bool | None = None


class JobPositionRead(BaseModel):
    model_config = _from_orm()

    id: int
    title: str
    department_id: int
    is_active: bool
    created_at: datetime
    updated_at: datetime
    department: DepartmentSummary | None = None


# ---------------------------------------------------------------------------
# Working Schedule (+ weekly pattern lines)
# ---------------------------------------------------------------------------

class WorkingScheduleLineCreate(BaseModel):
    day_of_week: int = Field(ge=0, le=6, description="0=Monday .. 6=Sunday")
    start_time: time
    end_time: time
    break_minutes: int = Field(default=0, ge=0)


class WorkingScheduleLineRead(BaseModel):
    model_config = _from_orm()

    id: int
    working_schedule_id: int
    day_of_week: int
    start_time: time
    end_time: time
    break_minutes: int


class WorkingScheduleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    schedule_type: ScheduleType
    company_id: int | None = None
    is_active: bool = True
    lines: list[WorkingScheduleLineCreate] = Field(default_factory=list)


class WorkingScheduleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    schedule_type: ScheduleType | None = None
    company_id: int | None = None
    is_active: bool | None = None


class WorkingScheduleListItem(BaseModel):
    """List view — includes computed total_weekly_hours but no lines."""

    model_config = _from_orm()

    id: int
    name: str
    schedule_type: ScheduleType
    company_id: int | None = None
    is_active: bool
    total_weekly_hours: Decimal
    created_at: datetime
    updated_at: datetime


class WorkingScheduleRead(BaseModel):
    """Detail view — includes the full weekly pattern `lines`."""

    model_config = _from_orm()

    id: int
    name: str
    schedule_type: ScheduleType
    company_id: int | None = None
    is_active: bool
    total_weekly_hours: Decimal
    lines: list[WorkingScheduleLineRead] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Employee
# ---------------------------------------------------------------------------

class ManagerRef(BaseModel):
    """id + name only — avoids over-fetching the full manager object."""

    id: int
    full_name: str


class WorkingScheduleRef(BaseModel):
    """id + name + derived hours for the employee form."""

    id: int
    name: str
    schedule_type: ScheduleType
    total_weekly_hours: Decimal


class RelatedSummary(BaseModel):
    """Smart-button badge counts (wireframe)."""

    contracts_count: int
    attendance_count: int
    time_off_count: int
    allocations_count: int


class EmployeeCreate(BaseModel):
    full_name: str = Field(min_length=1, max_length=150)
    work_email: EmailStr
    phone: str | None = Field(default=None, max_length=30)
    department_id: int
    job_position_id: int
    manager_id: int | None = None
    working_schedule_id: int
    employee_type: EmployeeType
    status: EmployeeStatus = EmployeeStatus.active
    date_of_joining: date
    work_location: str | None = Field(default=None, max_length=100)
    company_id: int | None = None


class EmployeeUpdate(BaseModel):
    """All-optional (PATCH). `manager_id`/`work_location`/`phone`/`company_id`
    are clearable — the service checks `model_fields_set` to distinguish
    "absent" (leave unchanged) from "explicitly null" (clear)."""

    full_name: str | None = Field(default=None, min_length=1, max_length=150)
    work_email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=30)
    department_id: int | None = None
    job_position_id: int | None = None
    manager_id: int | None = None
    working_schedule_id: int | None = None
    employee_type: EmployeeType | None = None
    status: EmployeeStatus | None = None
    date_of_joining: date | None = None
    work_location: str | None = Field(default=None, max_length=100)
    company_id: int | None = None


class EmployeeListItem(BaseModel):
    """Row for the List view and Kanban cards (grouped by status/department)."""

    id: int
    full_name: str
    work_email: str
    phone: str | None = None
    department: DepartmentSummary | None = None
    job_position: JobPositionSummary | None = None
    manager: ManagerRef | None = None
    employee_type: EmployeeType
    status: EmployeeStatus
    date_of_joining: date
    work_location: str | None = None


class EmployeeDetail(BaseModel):
    """Full Form payload: identity, work info, manager, schedule + smart-button
    counts and warnings (e.g. `["manager is inactive"]`)."""

    id: int
    full_name: str
    work_email: str
    phone: str | None = None
    department: DepartmentSummary | None = None
    job_position: JobPositionSummary | None = None
    manager: ManagerRef | None = None
    working_schedule: WorkingScheduleRef | None = None
    employee_type: EmployeeType
    status: EmployeeStatus
    date_of_joining: date
    work_location: str | None = None
    company_id: int | None = None
    related: RelatedSummary
    warnings: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Contract
# ---------------------------------------------------------------------------

class EmployeeSummary(BaseModel):
    id: int
    full_name: str
    work_email: str
    status: EmployeeStatus | None = None


class SalaryStructureSummary(BaseModel):
    id: int
    name: str
    code: str


class ContractCreate(BaseModel):
    """Creating a contract ALWAYS yields a `draft` — the only way to become
    `running` is POST /contracts/{id}/activate (spec §2.5)."""

    employee_id: int
    department_id: int
    job_position_id: int
    working_schedule_id: int
    salary_structure_id: int
    wage_monthly: WageDecimal
    start_date: date
    end_date: date | None = None


class ContractUpdate(BaseModel):
    """Edit while `draft` only (service rejects edits to running/expired/
    cancelled with 409). `end_date` is clearable via explicit null.

    `version_id` is the optimistic lock (arch doc §5.1): if the client sends
    a stale version the update is rejected with 409."""

    version_id: int | None = None
    department_id: int | None = None
    job_position_id: int | None = None
    working_schedule_id: int | None = None
    salary_structure_id: int | None = None
    wage_monthly: WageDecimal | None = None
    start_date: date | None = None
    end_date: date | None = None


class ContractActionRequest(BaseModel):
    """Body for activate / expire / cancel (optimistic lock version)."""

    version_id: int | None = None


class ContractRead(BaseModel):
    """Full form view. `employee` is a summary; department/job/schedule are
    nested summaries; salary structure is id+name+code."""

    id: int
    contract_number: str
    employee: EmployeeSummary | None = None
    department: DepartmentSummary | None = None
    job_position: JobPositionSummary | None = None
    working_schedule: WorkingScheduleRef | None = None
    salary_structure: SalaryStructureSummary | None = None
    wage_monthly: Decimal
    start_date: date
    end_date: date | None = None
    status: ContractStatus
    version_id: int
    created_at: datetime
    updated_at: datetime