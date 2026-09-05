"""Pydantic v2 request/response models for the Payroll module (Steve's slice).

Conventions (architecture doc §4):
- Money is `Decimal` (never float) — matches the NUMERIC(12,2) columns.
- Responses are plain Pydantic models, no hand-rolled {status, data} envelope.
- `SalaryRuleCreate/Update` enforce the "exactly one of amount/percentage/
  formula matching computation_method" rule in the API layer (model_validator)
  so a bad payload 422s before ever hitting Postgres.
- Dashboard schemas are plain aggregate DTOs, not ORM-backed.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_core import PydanticCustomError

from app.models.enums import (
    ComputationMethod,
    EmployeeType,
    PayslipWarningType,
    PayrunStatus,
    SalaryRuleCategory,
)

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    """Standard paginated envelope (architecture doc §4.4)."""

    items: list[T]
    total: int
    page: int
    page_size: int


# ---------------------------------------------------------------------------
# Salary Rules
# ---------------------------------------------------------------------------


class SalaryRuleBase(BaseModel):
    code: str = Field(
        pattern=r"^[A-Z][A-Z0-9_]*$",
        max_length=30,
        description="Unique uppercase code, e.g. BASIC / HRA / PF_DEDUCTION.",
    )
    name: str = Field(min_length=1, max_length=100)
    category: SalaryRuleCategory
    computation_method: ComputationMethod
    amount: Decimal | None = Field(default=None, ge=0)
    percentage: Decimal | None = Field(default=None, gt=0, le=100)
    percentage_base_code: str | None = Field(
        default=None,
        max_length=30,
        description="Rule code this percentage is computed against "
        "(e.g. BASIC). May reference the virtual CONTRACT_WAGE / WORKED_DAYS.",
    )
    formula: str | None = Field(
        default=None,
        description="Restricted Python expression over rule codes, e.g. "
        "'BASIC + HRA - PF_DEDUCTION'.",
    )
    default_sequence: int = Field(default=10, ge=0, le=32767)
    is_active: bool = True


class SalaryRuleCreate(SalaryRuleBase):
    @model_validator(mode="after")
    def _method_consistency(self) -> "SalaryRuleCreate":
        _validate_method_fields(self)
        return self


class SalaryRuleUpdate(BaseModel):
    """PATCH semantics — all optional. Method consistency is enforced against
    the merged row in the service layer (we can't see the existing row here)."""

    code: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]*$", max_length=30)
    name: str | None = Field(default=None, min_length=1, max_length=100)
    category: SalaryRuleCategory | None = None
    computation_method: ComputationMethod | None = None
    amount: Decimal | None = Field(default=None, ge=0)
    percentage: Decimal | None = Field(default=None, gt=0, le=100)
    percentage_base_code: str | None = Field(default=None, max_length=30)
    formula: str | None = Field(default=None)
    default_sequence: int | None = Field(default=None, ge=0, le=32767)
    is_active: bool | None = None

    @model_validator(mode="after")
    def _method_consistency(self) -> "SalaryRuleUpdate":
        # Only validate when the caller actually sets a computation method —
        # a partial PATCH (e.g. renaming) can't be fully checked here.
        if self.computation_method is not None:
            _validate_method_fields(self)
        return self


def _validate_method_fields(rule: BaseModel) -> None:
    """Enforce: exactly one of amount/percentage/formula is set, and it must
    match computation_method. Raises PydanticCustomError (a ValueError
    subclass) -> FastAPI 422, fail-fast before the DB CHECK constraint.

    PydanticCustomError is used (not bare ValueError) because Eldo's global
    RequestValidationError handler JSON-serializes `exc.errors()` verbatim,
    and pydantic embeds a bare ValueError object in the error ctx — which
    would crash with a 500. PydanticCustomError yields a serializable error.
    """
    method = rule.computation_method
    amount_set = rule.amount is not None
    percentage_set = rule.percentage is not None
    formula_set = rule.formula is not None
    set_count = int(amount_set) + int(percentage_set) + int(formula_set)

    def _error(message: str):
        raise PydanticCustomError("salary_rule_method_mismatch", message)

    if set_count != 1:
        _error(
            "Exactly one of amount / percentage / formula must be set "
            f"for computation_method='{method}'."
        )
    if method == ComputationMethod.fixed and not amount_set:
        _error("computation_method='fixed' requires 'amount'.")
    if method == ComputationMethod.percentage:
        if not percentage_set:
            _error(
                "computation_method='percentage' requires 'percentage' "
                "and 'percentage_base_code'."
            )
        if not rule.percentage_base_code:
            _error(
                "computation_method='percentage' requires 'percentage_base_code'."
            )
    if method == ComputationMethod.formula and not formula_set:
        _error("computation_method='formula' requires 'formula'.")


class SalaryRuleRead(SalaryRuleBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Salary Structures
# ---------------------------------------------------------------------------


class SalaryStructureCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$", max_length=30)
    company_id: int | None = None
    is_active: bool = True


class SalaryStructureUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    code: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]*$", max_length=30)
    company_id: int | None = None
    is_active: bool | None = None


class SalaryStructureRuleWrite(BaseModel):
    """One entry of the ordered rule list (PUT /salary-structures/{id}/rules)."""

    salary_rule_id: int
    sequence: int = Field(ge=0, le=32767)


class SalaryStructureRulesReplace(BaseModel):
    """Full replacement of a structure's ordered rule list — atomic, same
    'replace, don't patch piecemeal' pattern as Ameen's schedule lines."""

    rules: list[SalaryStructureRuleWrite] = Field(min_length=1)


class SalaryStructureRuleRead(BaseModel):
    """Nested item on GET /salary-structures/{id}: {sequence, rule: {...}}."""

    sequence: int
    rule: SalaryRuleRead


class SalaryStructureRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    code: str
    company_id: int | None = None
    is_active: bool
    rules: list[SalaryStructureRuleRead] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class SalaryStructureSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    code: str
    is_active: bool
    rule_count: int = 0


# ---------------------------------------------------------------------------
# Payrun wizard + lifecycle
# ---------------------------------------------------------------------------


class PayrunScope(BaseModel):
    """Wizard Step 1 payload — collected scope only, NO Payrun row is created.

    The frontend carries this forward to Step 2 (stateless wizard), so no
    orphaned draft rows are ever created server-side.
    """

    salary_structure_id: int
    period_start: date
    period_end: date
    department_filter_id: int | None = None
    employee_type_filter: EmployeeType | None = None
    name: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def _period_range(self) -> "PayrunScope":
        if self.period_end < self.period_start:
            raise ValueError("period_end must be >= period_start.")
        return self


class EligibleEmployeeOut(BaseModel):
    """Step 1 result row — what the frontend renders in Step 2's checkbox list."""

    id: int
    full_name: str
    work_email: str
    department_name: str
    employee_type: EmployeeType
    status: str
    has_contract: bool = Field(
        description="Whether a running/expired contract covers the payrun period "
        "(false => a missing_contract warning will surface on Compute)."
    )


class DraftScopeResponse(BaseModel):
    """POST /payruns/draft-scope — echoes the scope + the eligible employees."""

    scope: PayrunScope
    eligible_employees: list[EligibleEmployeeOut]
    eligible_count: int


class PayrunCreate(BaseModel):
    """Wizard Step 2 — scope (echoed from Step 1) + explicit employee_ids."""

    scope: PayrunScope
    employee_ids: list[int] = Field(min_length=1, description="At least one employee is required.")


class PayslipSummary(BaseModel):
    """Per-payslip summary embedded in GET /payruns/{id}."""

    id: int
    employee_id: int
    employee_name: str
    net_salary: Decimal
    status: PayrunStatus
    warning_count: int


class PayrunSummary(BaseModel):
    """List row for GET /payruns."""

    id: int
    name: str
    salary_structure_id: int
    period_start: date
    period_end: date
    department_filter_id: int | None = None
    employee_type_filter: EmployeeType | None = None
    status: PayrunStatus
    created_by_user_id: int
    payslip_count: int
    employee_count: int
    created_at: datetime


class PayrunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    salary_structure_id: int
    period_start: date
    period_end: date
    department_filter_id: int | None = None
    employee_type_filter: EmployeeType | None = None
    status: PayrunStatus
    created_by_user_id: int
    version_id: int
    created_at: datetime
    updated_at: datetime
    payslips: list[PayslipSummary] = Field(default_factory=list)


class ComputeSkippedItem(BaseModel):
    payslip_id: int
    employee_name: str
    reason: str


class ComputeResult(BaseModel):
    payrun_id: int
    status: PayrunStatus
    payslips_computed: int
    payslips_skipped: list[ComputeSkippedItem] = Field(default_factory=list)
    warnings_added: int


class ValidateResult(BaseModel):
    payrun_id: int
    status: PayrunStatus
    validated_payslips: int
    blocking_warnings: list[str] = Field(
        default_factory=list,
        description="List of blocking warnings that would block Validate "
        "(only present when Validate is refused).",
    )


class MarkPaidResult(BaseModel):
    payrun_id: int
    status: PayrunStatus
    paid_payslips: int


class CancelResult(BaseModel):
    payrun_id: int
    status: PayrunStatus
    cancelled_payslips: int


# ---------------------------------------------------------------------------
# Payslips
# ---------------------------------------------------------------------------


class PayslipLineRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    salary_rule_id: int
    sequence: int
    code: str
    name: str
    category: SalaryRuleCategory
    amount: Decimal


class PayslipWarningRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    warning_type: PayslipWarningType
    message: str
    created_at: datetime


class PayslipSummaryItem(BaseModel):
    """List row for GET /payslips."""

    id: int
    payrun_id: int
    employee_id: int
    employee_name: str
    period_start: date
    period_end: date
    gross_salary: Decimal
    net_salary: Decimal
    status: PayrunStatus
    warning_count: int


class PayslipRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    payrun_id: int
    employee_id: int
    employee_name: str
    contract_id: int | None = None
    period_start: date
    period_end: date
    worked_days: Decimal
    gross_salary: Decimal
    net_salary: Decimal
    status: PayrunStatus
    version_id: int
    created_at: datetime
    updated_at: datetime
    lines: list[PayslipLineRead] = Field(default_factory=list)
    warnings: list[PayslipWarningRead] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Payslip PDF + bulk email
# ---------------------------------------------------------------------------


class SendPayslipResultItem(BaseModel):
    employee_id: int
    employee_name: str
    sent: bool
    error: str | None = None


class SendPayslipsResult(BaseModel):
    payrun_id: int
    sent_count: int
    skipped_count: int
    results: list[SendPayslipResultItem] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Dashboard — plain aggregate DTOs (NOT ORM-backed)
# ---------------------------------------------------------------------------


class KpisResponse(BaseModel):
    total_net_salary_paid: Decimal
    payslips_generated: int
    average_salary: Decimal
    approved_time_off_days: Decimal
    attendance_health_pct: float


class SalaryByDepartmentItem(BaseModel):
    department_name: str
    total_salary: Decimal
    headcount: int


class MonthlyTrendItem(BaseModel):
    month: str  # ISO "YYYY-MM"
    total_net_salary: Decimal


class AttendanceOverview(BaseModel):
    present: int
    late: int
    absent: int
    overtime: int
    missing_checkouts: int
    manual_edits: int
    coverage_pct: float


class TimeOffBalanceItem(BaseModel):
    time_off_type_name: str
    remaining: Decimal


class TimeOffOverview(BaseModel):
    approved_days: Decimal
    pending_requests: int
    balances_by_type: list[TimeOffBalanceItem] = Field(default_factory=list)


class PayrollAlertItem(BaseModel):
    warning_type: PayslipWarningType
    count: int
    payslip_ids: list[int] = Field(default_factory=list)


class PayrollAlertsResponse(BaseModel):
    alerts: list[PayrollAlertItem] = Field(default_factory=list)
    total_open_payslips: int