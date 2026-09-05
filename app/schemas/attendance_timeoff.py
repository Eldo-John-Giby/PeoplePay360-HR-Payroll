"""Pydantic v2 request/response models for the Attendance & Time Off module
(Ambuj's slice).

Conventions (architecture doc §4) — same shape as Steve's `payroll.py`:
- `*Create` / `*Update` / `*Read` naming, `from_attributes=True` on reads.
- Plain responses, no hand-rolled {status, data} envelope.
- `Page[T]` generic pagination envelope duplicated per module (slices are
  disjoint — nobody imports another person's schema module).
- Durations / amounts are `Decimal` (matches NUMERIC(6,2) columns).

Notes specific to this module:
- `AttendanceRead.status` is the *effective* status the service computed for
  the row — an in-progress (`check_out IS NULL`) entry that has outlived its
  expected end-of-day + grace is displayed as `missing_checkout` without
  rewriting the stored value (see service docstring).
- `TimeOffBalanceRead` is a plain aggregate DTO built from the
  `v_time_off_balances` view — it is NOT backed by a single ORM model.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.enums import (
    AllocationStatus,
    AttendanceStatus,
    TimeOffRequestStatus,
    TimeOffUnit,
)

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    """Standard paginated envelope (architecture doc §4.4)."""

    items: list[T]
    total: int
    page: int
    page_size: int


# ===========================================================================
# Attendance
# ===========================================================================


class AttendanceRead(BaseModel):
    """Attendance row. `status` is the service-computed effective status;
    `employee_name` is joined in for list/detail screens."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    employee_id: int
    employee_name: str | None = None
    check_in: datetime
    check_out: datetime | None = None
    # Display hours — computed at check-out and stored (see attendance model).
    # None while the row is still open.
    worked_hours: Decimal | None = None
    status: AttendanceStatus
    is_manual_correction: bool
    corrected_by_user_id: int | None = None
    notes: str | None = None
    created_at: datetime
    updated_at: datetime


class AttendanceCheckInCreate(BaseModel):
    """POST /attendance/check-in.

    - `employee_id`: only meaningful for HR roles logging on someone's behalf
      (backfill). An EMPLOYEE never supplies it (forced to their own).
    - `check_in`: defaults to server now. Accepted naive (assumed UTC) or
      aware; the service normalizes to UTC immediately (service docstring
      documents the timezone simplification).
    """

    employee_id: int | None = None
    check_in: datetime | None = None
    notes: str | None = Field(default=None, max_length=500)


class AttendanceManualCreate(BaseModel):
    """POST /attendance — HR-only direct manual entry (both times given).
    The service stamps is_manual_correction=True + corrected_by_user_id."""

    employee_id: int
    check_in: datetime
    check_out: datetime
    notes: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _order(self) -> "AttendanceManualCreate":
        if self.check_out is not None and self.check_out < self.check_in:
            raise ValueError("check_out must be after check_in.")
        return self


class AttendanceUpdate(BaseModel):
    """PATCH /attendance/{id} — HR correction of an existing entry.

    All-optional PATCH semantics. `check_out=None` means "leave unchanged"
    (an open row cannot be made more open). The service re-derives
    worked_hours/status from the merged times and stamps the correction."""

    check_in: datetime | None = None
    check_out: datetime | None = None
    notes: str | None = Field(default=None, max_length=500)


class AttendanceSummaryRead(BaseModel):
    """GET /attendance/{employee_id}/summary — aggregate for smart-button /
    dashboard use.

    Absence has no synthetic rows (missing attendance == no row); `absent`
    here is derived as expected-scheduled-workdays minus attended days, the
    same diff Steve's payroll dashboard computes. `coverage_pct` = attended /
    expected * 100."""

    employee_id: int
    employee_name: str | None = None
    date_from: date
    date_to: date
    expected_workdays: int
    present: int
    late: int
    overtime: int
    missing_checkout: int
    absent: int
    coverage_pct: float


class MissingCheckoutSweepResult(BaseModel):
    """Result of the manual EOD sweep that stamps open entries as
    missing_checkout once their expected end-of-day + grace has passed."""

    swept: int


# ===========================================================================
# Time Off Types
# ===========================================================================


class TimeOffTypeBase(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    unit: TimeOffUnit
    requires_allocation: bool = True
    requires_approval: bool = True
    affects_payroll: bool = False
    company_id: int | None = None
    is_active: bool = True


class TimeOffTypeCreate(TimeOffTypeBase):
    pass


class TimeOffTypeUpdate(BaseModel):
    """PATCH semantics. Name uniqueness is checked in the service (the DB
    unique constraint treats NULL company rows as distinct, so we enforce
    scope-aware uniqueness ourselves)."""

    name: str | None = Field(default=None, min_length=1, max_length=100)
    unit: TimeOffUnit | None = None
    requires_allocation: bool | None = None
    requires_approval: bool | None = None
    affects_payroll: bool | None = None
    company_id: int | None = None
    is_active: bool | None = None


class TimeOffTypeRead(TimeOffTypeBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime


# ===========================================================================
# Time Off Allocations + balances
# ===========================================================================


class TimeOffAllocationCreate(BaseModel):
    """HR-only grant. Created as `to_approve` (no draft/submit step — keep
    the approval flow single-stage for the demo)."""

    employee_id: int
    time_off_type_id: int
    allocated_amount: Decimal = Field(gt=0)
    valid_from: date
    valid_to: date | None = None

    @model_validator(mode="after")
    def _validity_range(self) -> "TimeOffAllocationCreate":
        if self.valid_to is not None and self.valid_to < self.valid_from:
            raise ValueError("valid_to must be on or after valid_from.")
        return self


class TimeOffAllocationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    employee_id: int
    employee_name: str | None = None
    time_off_type_id: int
    type_name: str | None = None
    allocated_amount: Decimal
    valid_from: date
    valid_to: date | None = None
    status: AllocationStatus
    approver_id: int | None = None
    version_id: int
    created_at: datetime
    updated_at: datetime


class TimeOffBalanceRead(BaseModel):
    """Plain aggregate built from v_time_off_balances (allocated - taken),
    joined to the type + employee for display. Live-computed, never stored."""

    employee_id: int
    employee_name: str | None = None
    time_off_type_id: int
    type_name: str
    unit: TimeOffUnit
    allocated: Decimal
    taken: Decimal
    remaining: Decimal


# ===========================================================================
# Time Off Requests
# ===========================================================================


class TimeOffRequestCreate(BaseModel):
    """EMPLOYEE creates their own (`employee_id` forced to their own row);
    HR may pass `employee_id` to create on behalf of anyone.

    `duration` is in the type's `unit`. For `hours` types a duration that
    wildly exceeds the working hours across the span is surfaced as a *soft*
    warning (never a hard block) — half days etc. are legitimate."""

    employee_id: int | None = None
    time_off_type_id: int
    date_from: date
    date_to: date
    duration: Decimal = Field(gt=0)
    reason: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def _date_range(self) -> "TimeOffRequestCreate":
        if self.date_to < self.date_from:
            raise ValueError("date_to must be on or after date_from.")
        return self


class TimeOffRequestRead(BaseModel):
    """Request row. `warnings` is only populated on the responses of the
    actions that surface them (create / approve); list reads leave it empty
    to keep read paths cheap (see service docstring)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    employee_id: int
    employee_name: str | None = None
    time_off_type_id: int
    type_name: str | None = None
    unit: TimeOffUnit | None = None
    date_from: date
    date_to: date
    duration: Decimal
    status: TimeOffRequestStatus
    approver_id: int | None = None
    reason: str | None = None
    warnings: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
