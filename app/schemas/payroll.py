"""Pydantic v2 request/response models for the Payroll module (Steve's slice).

CONNECTIONS MAP (read this first):
- WHO USES ME: the routers app/modules/payroll/router.py and
  dashboard_router.py declare `response_model=...` / body-parse with these
  classes; the service layer builds them from ORM rows (model_validate for
  from_attributes models, or manual construction for aggregates).
- IMPORTED FROM: app/models/enums.py (the shared Python enums that mirror
  the Postgres-native ENUM types Eldo created) and pydantic v2.
- WHY THEY EXIST: (1) FastAPI validates request bodies and serializes
  responses through these classes — the contract shown in /docs;
  (2) validation FAILS FAST here (422) instead of after a DB round-trip;
  (3) ORM objects are never leaked to the client.
- RULE: request models validate user input; *Read models (from_attributes)
  mirror ORM rows; result DTOs (ComputeResult, SendPayslipsResult...) shape
  action responses; dashboard classes are plain aggregate DTOs.

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
    """Standard paginated envelope (architecture doc §4.4).

    Generic so every list endpoint returns a typed page (e.g.
    Page[PayslipSummaryItem]). Every list endpoint in the module uses it:
    salary rules/structures, payruns, payslips, /payslips/me."""

    items: list[T]
    total: int
    page: int
    page_size: int


# ---------------------------------------------------------------------------
# Salary Rules
# ---------------------------------------------------------------------------
# One configurable atomic line of pay: fixed amount | % of another code |
# restricted formula. salary_rules is the GLOBAL rule library; whether a rule
# participates in a structure (and in what order) is decided by the
# salary_structure_rules junction in the next section.


class SalaryRuleBase(BaseModel):
    """Shared fields of a salary rule. One rule = one atomic computation line:
    it computes EITHER a fixed amount, OR a percentage of another rule's
    computed value (percentage_base_code), OR a restricted formula over
    previously-computed codes. Which of the three is enforced by
    computation_method + the model_validators below (and again by a DB CHECK
    constraint on salary_rules in Eldo's schema).

    code format is UPPER_SNAKE ([A-Z][A-Z0-9_]*) — matches the seeded
    conventions (BASIC, HRA, PF_DEDUCTION) and is what formulas reference."""

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
    # Unlike Create, we CANNOT fully validate here: a partial PATCH might only
    # rename the rule. So the validator runs only when the caller actually
    # sets computation_method, and service.update_salary_rule re-checks the
    # MERGED row against _validate_rule_consistency before committing.

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
    """Read response for a salary rule; from_attributes=True lets the service
    pass an ORM SalaryRule straight into model_validate()."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Salary Structures
# ---------------------------------------------------------------------------
# A named ORDERED chain of salary rules (via SalaryStructureRuleWrite +
# SalaryStructureRulesReplace). GET /salary-structures/{id} returns the nested
# ordered list; PUT .../rules replaces it atomically.


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
    """One entry of the ordered rule list (PUT /salary-structures/{id}/rules).

    `sequence` is what makes a structure ORDERED — the engine executes rules
    in ascending sequence order and later rules can reference earlier ones."""

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
    """Structure detail: nested `rules: [{sequence, rule: {...}}]` ordered by
    sequence — exactly the order the engine executes them in."""

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

    Fields: which salary structure + which period + optional department /
    employee-type filters. Same object is re-sent in PayrunCreate (Step 2)
    so the server can re-validate the scope on creation (anti-tampering).
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
    """Wizard Step 2 — scope (echoed from Step 1) + explicit employee_ids.
    The service re-validates every id against the scope filters before
    creating the Payrun row (defense against a tampered frontend)."""

    scope: PayrunScope
    employee_ids: list[int] = Field(min_length=1, description="At least one employee is required.")


class PayslipSummary(BaseModel):
    """Per-payslip summary embedded in GET /payruns/{id}.
    warning_count EXCLUDES internal SENT_AT sentinels (service filters them)."""

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
    """POST /payruns/{id}/compute response: how many payslips were (re)computed,
    which were skipped because they were already validated/paid (never touch
    finalized history), and how many warnings were added overall."""

    payrun_id: int
    status: PayrunStatus
    payslips_computed: int
    payslips_skipped: list[ComputeSkippedItem] = Field(default_factory=list)
    warnings_added: int


class ValidateResult(BaseModel):
    """POST /payruns/{id}/validate response. When validation is REFUSED the
    endpoint raises 409 with the blocking-warning list in the error; this DTO
    carries the happy-path counts."""

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
    """List row for GET /payslips. Same shape reused by GET /payslips/me
    (service.list_payslips filters by employee_id for the current user)."""

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
    """Full payslip: the computed breakdown the UI shows on detail screens
    and the payslip PDF draws from. Nests the ordered lines (snapshots of
    the salary rules at compute time) and warnings. contract_id is nullable
    because an employee with no applicable contract still gets a zero-value
    payslip carrying a missing_contract warning."""

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
# Per-recipient results (never all-or-nothing): every send/skip/failure is
# reported for one employee so one bad address can't mask the others.


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
# These exist to type the read-only aggregation queries in service.py for the
# six /api/v1/dashboard endpoints. They are hand-built by the service layer
# (no ConfigDict(from_attributes=True)) because there is no single ORM row
# behind them.


class KpisResponse(BaseModel):
    """Dashboard KPI cards. Plain aggregate DTO — NOT ORM-backed (dashboard
    schemas are deliberately plain per the module docstring)."""

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
    """One point of the monthly line chart. month is ISO "YYYY-MM"; months
    with no paid payslips still appear with total 0 (charts need no gaps)."""

    month: str  # ISO "YYYY-MM"
    total_net_salary: Decimal


class AttendanceOverview(BaseModel):
    """Attendance Overview panel: live counts from Ambuj's attendances +
    schedule-derived absent/coverage. absent = expected schedule days minus
    attended (no synthetic absent rows exist in the table); manual_edits
    counts rows stamped is_manual_correction."""

    present: int
    late: int
    absent: int
    overtime: int
    missing_checkouts: int
    manual_edits: int
    coverage_pct: float


class PayslipStatusOverview(BaseModel):
    """Payslip Status panel — live distribution of payslip lifecycle states
    (paid / validated / computed / draft / cancelled) within the filters.
    unvalidated = draft + computed (money not yet signed off);
    with_warnings = distinct draft/computed payslips carrying >=1 real (non-
    SENT_AT-sentinel) warning. Both drive the "Pending / Warning" columns."""

    draft: int = 0
    computed: int = 0
    validated: int = 0
    paid: int = 0
    cancelled: int = 0
    unvalidated: int = 0
    with_warnings: int = 0


class TimeOffBalanceItem(BaseModel):
    time_off_type_name: str
    remaining: Decimal


class TimeOffTypeOverviewItem(BaseModel):
    """One row of the Time Off Overview table: per-type approved days (in the
    period) + pending request count + LIVE remaining balance (from the
    v_time_off_balances view — never stored). approved_days only sums day-
    unit types (hour-unit leave can't add into a 'days' number)."""

    time_off_type_name: str
    approved_days: Decimal
    pending_requests: int
    remaining: Decimal


class TimeOffOverview(BaseModel):
    """Time Off Overview: totals + per-type rows. approved_days/pending_requests
    respect the period + department/type/company filters; remaining balances
    are the live current balances of the filtered employees."""

    approved_days: Decimal
    pending_requests: int
    balances_by_type: list[TimeOffBalanceItem] = Field(default_factory=list)
    by_type: list[TimeOffTypeOverviewItem] = Field(default_factory=list)


class PayrollAlertItem(BaseModel):
    warning_type: PayslipWarningType
    count: int
    payslip_ids: list[int] = Field(default_factory=list)


class PayrollAlertsResponse(BaseModel):
    """Dashboard 'payroll alerts' payload: warnings grouped by type with
    counts + payslip ids to drill into, plus how many draft/computed payslips
    are currently carrying at least one open warning, and the total number of
    UNVALIDATED payslips (draft/computed — money that still needs to be
    signed off)."""

    alerts: list[PayrollAlertItem] = Field(default_factory=list)
    total_open_payslips: int
    unvalidated_payslips: int = 0


class FilterOptionItem(BaseModel):
    """One selectable option in the dashboard filter bar (company or
    department). `company_id` lets the UI disable departments that belong to
    another company when a company filter is active."""

    id: int
    name: str
    company_id: int | None = None


class DashboardFilterOptionsResponse(BaseModel):
    """GET /dashboard/filter-options — the option lists that power the
    composable filter bar: companies, departments and employee types.
    Employee types are enum values from the DB enum; companies/departments
    are live rows (never hardcoded)."""

    companies: list[FilterOptionItem] = Field(default_factory=list)
    departments: list[FilterOptionItem] = Field(default_factory=list)
    employee_types: list[str] = Field(default_factory=list)