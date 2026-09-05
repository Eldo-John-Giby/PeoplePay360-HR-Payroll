"""Service layer for the Payroll module (Steve's slice).

CONNECTIONS MAP (read this first):
- WHO CALLS ME: the routers in this module (app/modules/payroll/router.py and
  dashboard_router.py) — every endpoint is a one-line passthrough into a
  function here. Nothing outside app/modules/payroll/** calls me.
- WHAT I CALL: app/modules/payroll/engine.py (compute_payslip_for_employee /
  PayrollEngineError) and app/modules/payroll/pdf.py (render/send).
- WHAT I READ (read-only, across ALL team members' tables): Eldo's
  payroll/auth/models, Ameen's Employee/Contract/WorkingSchedule/
  Department/Company, Ambuj's Attendance + TimeOff* + the v_time_off_balances
  view. NEVER writes to any table outside payroll.*.
- WHAT I WRITE: Payrun, PayrunEmployee, Payslip, PayslipLine, PayslipWarning,
  SalaryRule, SalaryStructure, SalaryStructureRule (all in app/models/
  payroll.py — Eldo's schema, imported as-is).
- WHY EVERYTHING LIVES HERE (not in the routers): arch §4.3 layering —
  business rules are unit-testable without FastAPI; routers stay thin.
  tests/test_payroll.py calls these functions directly against Postgres.

Business rules (from 00_ARCHITECTURE_AND_WORKFLOW.md / the Steve prompt):
- Salary Structures & Rules: RBAC-gated CRUD; `PUT /salary-structures/{id}/rules`
  replaces the ordered rule list atomically (duplicate rule in payload -> 409).
- Payrun 2-step wizard: `draft-scope` computes eligible employees WITHOUT
  creating a Payrun row (stateless wizard); `create_payrun` creates the row +
  payrun_employees in one transaction and validates every employee_id against
  the scope (tampered-frontend defense).
- Compute: idempotent — replaces lines/warnings of draft/computed payslips
  (delete-then-reinsert), skips finalized (validated/paid) ones and reports
  them. Concurrent compute is guarded by the Payrun `version_id` optimistic
  lock -> StaleDataError -> 409.
- Validate: blocked while any BLOCKING warning is open (negative_net,
  missing_contract). missing_bank_details does NOT block validating amounts —
  it blocks *sending* payslips.
- mark-paid: only from validated; idempotency guard -> 409 on double call.
- Bulk send: per-employee results, never all-or-nothing; one bad recipient
  (missing bank details / email / SMTP error) is skipped and reported.
- Overlapping-period detection across payruns -> warning on both payslips
  (the other only when it is still draft/computed — finalized ones are
  historical and never touched).

Sent-at idempotency note: the schema has no `payslips.sent_at` column (Eldo
owns models/). Until Eldo adds one, send state is tracked with a PayslipWarning
of type `other` whose message starts with the SENT_AT_SENTINEL prefix. These
sentinel rows are filtered out of every read path, so they never pollute the
warnings UI.
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.orm.exc import StaleDataError

from app.core.exceptions import (
    ConflictException,
    ForbiddenException,
    NotFoundException,
    ValidationException,
)
from app.models.attendance import Attendance
from app.models.auth import Role, User
from app.models.employee import Contract, Employee
from app.models.enums import (
    AttendanceStatus,
    ContractStatus,
    EmployeeStatus,
    EmployeeType,
    PayslipWarningType,
    PayrunStatus,
    SalaryRuleCategory,
)
from app.models.organization import Company, Department, WorkingSchedule
from app.models.payroll import (
    Payrun,
    PayrunEmployee,
    Payslip,
    PayslipLine,
    PayslipWarning,
    SalaryRule,
    SalaryStructure,
    SalaryStructureRule,
)
from app.models.timeoff import TimeOffRequest, TimeOffType
from app.models.views import TimeOffBalanceView
from app.schemas.payroll import (
    AttendanceOverview,
    CancelResult,
    ComputeResult,
    ComputeSkippedItem,
    DraftScopeResponse,
    EligibleEmployeeOut,
    KpisResponse,
    MarkPaidResult,
    MonthlyTrendItem,
    Page,
    PayrollAlertItem,
    PayrollAlertsResponse,
    PayrunCreate,
    PayrunRead,
    PayrunScope,
    PayrunSummary,
    PayslipLineRead,
    PayslipRead,
    PayslipSummary,
    PayslipSummaryItem,
    PayslipWarningRead,
    SalaryByDepartmentItem,
    SalaryRuleCreate,
    SalaryRuleRead,
    SalaryRuleUpdate,
    SalaryStructureCreate,
    SalaryStructureRead,
    SalaryStructureRuleRead,
    SalaryStructureSummary,
    SalaryStructureUpdate,
    SendPayslipResultItem,
    SendPayslipsResult,
    TimeOffBalanceItem,
    TimeOffOverview,
    ValidateResult,
)
from app.modules.payroll import pdf as pdf_mod
from app.modules.payroll.engine import (
    PayrollEngineError,
    compute_payslip_for_employee,
)

# ---------------------------------------------------------------------------
# Module constants — read these first; they encode the module's policy
# ---------------------------------------------------------------------------
# BLOCKING_WARNING_TYPES: which payslip warnings stop service.validate_payrun.
# The chosen two are amounts-correctness problems (negative net = wrong
# amount; missing contract = salary is unbacked/zero). missing_bank_details is
# intentionally NOT blocking — it blocks *sending money/payslips*, not
# validating the computed amount. (Documented in the prompt §3.3; keep this
# set in sync with engine.py's warning emitters.)

# Warning types that BLOCK Validate (documented in the module docstring).
BLOCKING_WARNING_TYPES = {
    PayslipWarningType.negative_net,
    PayslipWarningType.missing_contract,
}

# Roles that get full payroll access. EMPLOYEE gets only own-payslip views;
# HR_MANAGER has NO payroll access at all (architecture doc §4.7).
PAYROLL_ROLES = {"HR_PAYROLL_USER", "HR_PAYROLL_MANAGER", "ADMIN"}

# Sentinel prefix for the internal "payslip sent" marker (see module docstring).
# Why: Eldo's schema has no payslips.sent_at column and we may not touch
# models/. So bulk-email idempotency is tracked by inserting a PayslipWarning
# of type `other` whose message starts with SENT_AT_SENTINEL + a timestamp.
# Every read path filters these out via _is_sentinel() so they never surface
# in the UI, but a second send_payslips() call sees them and skips.
SENT_AT_SENTINEL = "SENT_AT:"

# Bulk-send / PDF generation runs in a privileged internal context.
# Why: sending payslips must bypass the per-user ownership check in
# get_payslip_pdf (the caller HR user may not "own" every payslip, but the
# batch endpoint is already RBAC-gated). A synthetic ADMIN user with id 0
# (never a real users row) is used purely as an in-process permission token.
SYSTEM_USER_ID = 0


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Shared helpers used all over this module
# ---------------------------------------------------------------------------


def _clamp_page(page: int, page_size: int) -> tuple[int, int]:
    """Pagination clamping: page >= 1, 1 <= page_size <= 200 (§4.4/§5.5).
    Cross-cutting edge case §5.5: page=0 / negative / oversized values must be
    clamped, not crash the query."""
    return max(page, 1), min(max(page_size, 1), 200)


def _paginate(db: Session, stmt, page: int, page_size: int) -> tuple[list, int, int, int]:
    """Run a select statement with the shared pagination envelope. Counts the
    total rows WITHOUT the LIMIT/OFFSET (via a subquery), then fetches the
    page. Returns (items, total, page, page_size) for the Page[...] schemas."""
    page, page_size = _clamp_page(page, page_size)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    items = list(db.scalars(stmt.offset((page - 1) * page_size).limit(page_size)).all())
    return items, total, page, page_size


def _default_payrun_name(period_start: date) -> str:
    """Auto name when the wizard user didn't supply one: 'Payrun — August 2026'."""
    return f"Payrun — {period_start.strftime('%B %Y')}"


def _is_sentinel(warning: PayslipWarning) -> bool:
    """True if a PayslipWarning is actually the internal SENT_AT marker (not a
    real payroll warning). Every read path filters these out."""
    return warning.message.startswith(SENT_AT_SENTINEL)


def _user_has_payroll_role(user: User) -> bool:
    """Does the user hold any payroll role? Used by can_access_payslip to
    decide whether they may see payslips other than their own."""
    return bool({r.name for r in user.roles} & PAYROLL_ROLES)

def _system_user() -> User:
    """Privileged internal context for bulk-send PDF generation (the batch
    endpoint is already gated to payroll roles by the router)."""
    return User(id=SYSTEM_USER_ID, email="system@peoplepay360.local",
                roles=[Role(name="ADMIN")])


# ---------------------------------------------------------------------------
# Salary Rules CRUD (router: /api/v1/payroll/salary-rules)
# ---------------------------------------------------------------------------
# salary_rules is the global, reusable rule library. Writes are MANAGER/ADMIN
# only (RBAC enforced in the router); this service layer enforces code
# uniqueness and the CHECK-constraint consistency on every write path.


def get_salary_rule_or_404(db: Session, rule_id: int) -> SalaryRule:
    """Shared lookup used by get/update/delete; 404s with a friendly message
    instead of letting a raw .get() None propagate."""
    rule = db.get(SalaryRule, rule_id)
    if rule is None:
        raise NotFoundException(f"Salary rule {rule_id} not found.")
    return rule


def list_salary_rules(
    db: Session,
    page: int = 1,
    page_size: int = 20,
    code: str | None = None,
    category: SalaryRuleCategory | None = None,
    is_active: bool | None = None,
) -> Page[SalaryRuleRead]:
    """List the global rule library with optional code (ilike)/category/
    is_active filters + pagination. Powers the config management screen."""
    stmt = select(SalaryRule).order_by(SalaryRule.id)
    if code:
        stmt = stmt.where(SalaryRule.code.ilike(f"%{code}%"))
    if category is not None:
        stmt = stmt.where(SalaryRule.category == category)
    if is_active is not None:
        stmt = stmt.where(SalaryRule.is_active.is_(is_active))
    items, total, page, page_size = _paginate(db, stmt, page, page_size)
    return Page[SalaryRuleRead](
        items=[SalaryRuleRead.model_validate(r) for r in items],
        total=total,
        page=page,
        page_size=page_size,
    )


def get_salary_rule(db: Session, rule_id: int) -> SalaryRuleRead:
    """Single rule read (used when editing / by GET detail)."""
    return SalaryRuleRead.model_validate(get_salary_rule_or_404(db, rule_id))


def create_salary_rule(db: Session, payload: SalaryRuleCreate) -> SalaryRuleRead:
    """Create one global salary rule. code is UNIQUE (DB unique index + this
    explicit pre-check) — formulas/percentages reference codes, so a
    duplicate code would silently mis-route computations. Pydantic already
    enforced method/field consistency in SalaryRuleCreate before we get here."""
    if db.scalar(select(SalaryRule).where(SalaryRule.code == payload.code)):
        raise ConflictException(
            f"A salary rule with code '{payload.code}' already exists."
        )
    rule = SalaryRule(**payload.model_dump())
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return SalaryRuleRead.model_validate(rule)


def update_salary_rule(
    db: Session, rule_id: int, payload: SalaryRuleUpdate
) -> SalaryRuleRead:
    """PATCH a rule. exclude_unset=True means only fields the client actually
    sent are applied (true PATCH semantics). Changing `code` re-checks global
    uniqueness; every merge is re-validated against the CHECK constraint via
    _validate_rule_consistency because a PATCH can't be fully validated by
    pydantic alone (the method may flip alongside its fields)."""
    rule = get_salary_rule_or_404(db, rule_id)
    changes = payload.model_dump(exclude_unset=True)
    if "code" in changes and changes["code"] != rule.code:
        if db.scalar(
            select(SalaryRule).where(
                SalaryRule.code == changes["code"], SalaryRule.id != rule.id
            )
        ):
            raise ConflictException(
                f"A salary rule with code '{changes['code']}' already exists."
            )
    for field, value in changes.items():
        setattr(rule, field, value)
    _validate_rule_consistency(rule)
    db.commit()
    db.refresh(rule)
    return SalaryRuleRead.model_validate(rule)


def _validate_rule_consistency(rule: SalaryRule) -> None:
    """Re-check the DB CHECK constraint in the service layer after merges
    (PATCH payloads may have changed computation_method + fields separately).
    Mirrors schemas._validate_method_fields but works on the MERGED ORM row,
    so it is the last line of defense before the Postgres CHECK constraint
    (never leak raw IntegrityError text — §3.2 edge case)."""
    amount_set = rule.amount is not None
    percentage_set = rule.percentage is not None
    formula_set = rule.formula is not None
    set_count = int(amount_set) + int(percentage_set) + int(formula_set)
    method = rule.computation_method
    ok = (
        set_count == 1
        and (
            (method.value == "fixed" and amount_set)
            or (method.value == "percentage" and percentage_set and rule.percentage_base_code)
            or (method.value == "formula" and formula_set)
        )
    )
    if not ok:
        raise ValidationException(
            "Salary rule must set exactly one of amount/percentage/formula "
            "matching its computation_method."
        )


def delete_salary_rule(db: Session, rule_id: int) -> SalaryRuleRead:
    """Soft delete (is_active=False) — hard deletes are protected by
    ON DELETE RESTRICT from salary_structure_rules anyway.
    Why soft (arch §4.5 + prompt §3.2): a rule referenced by an active
    structure must not disappear and silently rewrite history; deactivation
    stops it being USED in future computes while rows keep their FK."""
    rule = get_salary_rule_or_404(db, rule_id)
    rule.is_active = False
    db.commit()
    db.refresh(rule)
    return SalaryRuleRead.model_validate(rule)


# ---------------------------------------------------------------------------
# Salary Structures CRUD + ordered rules (router: /salary-structures)
# ---------------------------------------------------------------------------
# A structure is a named ORDERED chain of salary rules. Reads are open to
# HR_PAYROLL_USER+; writes (incl. the rules replace) to MANAGER/ADMIN only.


def get_salary_structure_or_404(db: Session, structure_id: int) -> SalaryStructure:
    """Shared lookup for get/update/delete/replace_structure_rules and scope
    validation; raises NotFoundException instead of returning None."""
    structure = db.get(SalaryStructure, structure_id)
    if structure is None:
        raise NotFoundException(f"Salary structure {structure_id} not found.")
    return structure


def list_salary_structures(
    db: Session,
    page: int = 1,
    page_size: int = 20,
    is_active: bool | None = None,
) -> Page[SalaryStructureSummary]:
    """List structures with a live rule_count (correlated scalar subquery —
    one COUNT per structure, no N+1). Summary rows feed the config UI."""
    rule_count = (
        select(func.count())
        .select_from(SalaryStructureRule)
        .where(SalaryStructureRule.salary_structure_id == SalaryStructure.id)
        .scalar_subquery()
    )
    stmt = select(SalaryStructure, rule_count.label("rule_count")).order_by(SalaryStructure.id)
    if is_active is not None:
        stmt = stmt.where(SalaryStructure.is_active.is_(is_active))
    page, page_size = _clamp_page(page, page_size)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.execute(
        stmt.offset((page - 1) * page_size).limit(page_size)
    ).all()
    items = [
        SalaryStructureSummary(
            id=s.id, name=s.name, code=s.code, is_active=s.is_active,
            rule_count=rule_count_val,
        )
        for s, rule_count_val in rows
    ]
    return Page[SalaryStructureSummary](
        items=items, total=total, page=page, page_size=page_size
    )


def get_salary_structure(db: Session, structure_id: int) -> SalaryStructureRead:
    """Detail read: structure + its ordered rules, eager-loaded via
    selectinload so listing rules doesn't N+1. Returns rules sorted by
    (sequence, id) — the canonical execution order the engine will follow."""
    structure = db.scalar(
        select(SalaryStructure)
        .options(
            selectinload(SalaryStructure.rules).selectinload(SalaryStructureRule.salary_rule)
        )
        .where(SalaryStructure.id == structure_id)
    )
    if structure is None:
        raise NotFoundException(f"Salary structure {structure_id} not found.")
    ordered = sorted(structure.rules, key=lambda sr: (sr.sequence, sr.id))
    return SalaryStructureRead(
        id=structure.id,
        name=structure.name,
        code=structure.code,
        company_id=structure.company_id,
        is_active=structure.is_active,
        created_at=structure.created_at,
        updated_at=structure.updated_at,
        rules=[
            SalaryStructureRuleRead(
                sequence=sr.sequence,
                rule=SalaryRuleRead.model_validate(sr.salary_rule),
            )
            for sr in ordered
        ],
    )


def create_salary_structure(
    db: Session, payload: SalaryStructureCreate
) -> SalaryStructureRead:
    """Create a structure SHELL (no rules yet — rules are attached via
    replace_structure_rules). code uniqueness pre-checked like salary rules."""
    if db.scalar(select(SalaryStructure).where(SalaryStructure.code == payload.code)):
        raise ConflictException(
            f"A salary structure with code '{payload.code}' already exists."
        )
    structure = SalaryStructure(**payload.model_dump())
    db.add(structure)
    db.commit()
    db.refresh(structure)
    return SalaryStructureRead(
        id=structure.id,
        name=structure.name,
        code=structure.code,
        company_id=structure.company_id,
        is_active=structure.is_active,
        created_at=structure.created_at,
        updated_at=structure.updated_at,
        rules=[],
    )


def update_salary_structure(
    db: Session, structure_id: int, payload: SalaryStructureUpdate
) -> SalaryStructureRead:
    """PATCH structure metadata (name/code/company/is_active) — does NOT touch
    the ordered rules (that is replace_structure_rules' job). After commit it
    re-reads the detail so the response includes the nested rules."""
    structure = get_salary_structure_or_404(db, structure_id)
    changes = payload.model_dump(exclude_unset=True)
    if "code" in changes and changes["code"] != structure.code:
        if db.scalar(
            select(SalaryStructure).where(
                SalaryStructure.code == changes["code"],
                SalaryStructure.id != structure.id,
            )
        ):
            raise ConflictException(
                f"A salary structure with code '{changes['code']}' already exists."
            )
    for field, value in changes.items():
        setattr(structure, field, value)
    db.commit()
    return get_salary_structure(db, structure_id)


def delete_salary_structure(db: Session, structure_id: int) -> None:
    """Soft delete — structures referenced by contracts are protected by
    ON DELETE RESTRICT; history is preserved (§4.5)."""
    structure = get_salary_structure_or_404(db, structure_id)
    structure.is_active = False
    db.commit()


def replace_structure_rules(
    db: Session, structure_id: int, rules: list[dict]
) -> SalaryStructureRead:
    """PUT /salary-structures/{id}/rules — atomic full replacement.

    Same 'replace, don't patch piecemeal' pattern as Ameen's working-schedule
    lines: avoids sequence-gap bugs. Duplicate salary_rule_id in the payload
    -> 409 (mirrors Eldo's UNIQUE constraint, translated cleanly).
    """
    get_salary_structure_or_404(db, structure_id)

    # Pre-flight checks in ONE pass: reject duplicate rule ids in the payload
    # (409, mirroring Eldo's UNIQUE(salary_structure_id, salary_rule_id)),
    # unknown ids (404), and inactive rules (409) before touching any rows.
    seen: set[int] = set()
    for item in rules:
        if item["salary_rule_id"] in seen:
            raise ConflictException(
                f"Salary rule {item['salary_rule_id']} is attached more than "
                "once to this structure."
            )
        seen.add(item["salary_rule_id"])

    rule_ids = list(seen)
    found_rules = {
        r.id: r
        for r in db.scalars(select(SalaryRule).where(SalaryRule.id.in_(rule_ids))).all()
    }
    missing = [rid for rid in rule_ids if rid not in found_rules]
    if missing:
        raise NotFoundException(
            f"Salary rule(s) not found: {', '.join(str(m) for m in missing)}."
        )
    inactive = [rid for rid in rule_ids if not found_rules[rid].is_active]
    if inactive:
        raise ConflictException(
            f"Inactive salary rule(s) cannot be attached: "
            f"{', '.join(str(m) for m in inactive)}."
        )

    # Atomic replace inside one transaction: delete every existing junction
    # row for this structure, flush, then insert the new ordered list. If
    # anything fails mid-way the whole transaction rolls back (no partial
    # states / sequence gaps).
    for old in list(
        db.scalars(
            select(SalaryStructureRule).where(
                SalaryStructureRule.salary_structure_id == structure_id
            )
        ).all()
    ):
        db.delete(old)
    db.flush()
    for item in rules:
        db.add(
            SalaryStructureRule(
                salary_structure_id=structure_id,
                salary_rule_id=item["salary_rule_id"],
                sequence=item["sequence"],
            )
        )
    db.commit()
    return get_salary_structure(db, structure_id)


# ---------------------------------------------------------------------------
# Payrun wizard (Steps 1 & 2) — the 2-step stateless flow
# ---------------------------------------------------------------------------
# Step 1 (draft_scope) only VALIDATES + PREVIEWS who is eligible. Step 2
# (create_payrun) is the only place a Payrun row is born — always draft.


def _validate_scope(db: Session, scope: PayrunScope) -> None:
    """Shared scope sanity checks used by BOTH wizard steps: the salary
    structure must exist + be active, and the department filter (if any) must
    reference a real department (referential-existence rule, arch §5.3)."""
    structure = get_salary_structure_or_404(db, scope.salary_structure_id)
    if not structure.is_active:
        raise ConflictException(
            f"Salary structure '{structure.code}' is inactive."
        )
    if scope.department_filter_id is not None:
        dept = db.get(Department, scope.department_filter_id)
        if dept is None:
            raise NotFoundException(
                f"Department {scope.department_filter_id} not found."
            )


def _contract_overlaps_period_stmt(period_start: date, period_end: date):
    """EXISTS subquery: employee has a running/expired contract whose range
    overlaps [period_start, period_end].
    Used ONLY by draft_scope to compute each eligible employee's `has_contract`
    flag (read-only into Ameen's contracts table). Same overlap semantics as
    engine.resolve_applicable_contract — inclusive on both ends."""
    return (
        select(Contract.id)
        .where(
            Contract.employee_id == Employee.id,
            Contract.status.in_([ContractStatus.running, ContractStatus.expired]),
            Contract.start_date <= period_end,
            or_(Contract.end_date.is_(None), Contract.end_date >= period_start),
        )
        .exists()
    )


def draft_scope(db: Session, scope: PayrunScope) -> DraftScopeResponse:
    """Wizard Step 1 — DOES NOT create a Payrun row (stateless wizard).
    Returns the scope echoed back + eligible employees for Step 2.

    Eligibility = active employees matching the department/type filters (the
    wireframe's Step-2 checkbox list). has_contract comes from an EXISTS
    against contracts covering the period — false means Compute will emit a
    missing_contract warning, so the UI can warn BEFORE the user commits.
    Everything is read-only; the frontend carries the scope forward."""
    _validate_scope(db, scope)

    stmt = (
        select(
            Employee,
            Department.name.label("department_name"),
            _contract_overlaps_period_stmt(scope.period_start, scope.period_end).label("has_contract"),
        )
        .join(Department, Department.id == Employee.department_id)
        .where(Employee.status == EmployeeStatus.active)
        .order_by(Employee.full_name)
    )
    if scope.department_filter_id is not None:
        stmt = stmt.where(Employee.department_id == scope.department_filter_id)
    if scope.employee_type_filter is not None:
        stmt = stmt.where(Employee.employee_type == scope.employee_type_filter)

    rows = db.execute(stmt).all()
    eligible = [
        EligibleEmployeeOut(
            id=emp.id,
            full_name=emp.full_name,
            work_email=emp.work_email,
            department_name=dept_name,
            employee_type=emp.employee_type,
            status=emp.status.value,
            has_contract=bool(has_contract),
        )
        for emp, dept_name, has_contract in rows
    ]
    return DraftScopeResponse(
        scope=scope, eligible_employees=eligible, eligible_count=len(eligible)
    )


def create_payrun(db: Session, payload: PayrunCreate, current_user: User) -> PayrunRead:
    """Wizard Step 2 — creates the Payrun (draft) + payrun_employees rows in
    ONE transaction. Every employee_id is validated against the scope
    (tampered-frontend defense)."""
    scope = payload.scope
    _validate_scope(db, scope)

    # Cross-cutting edge case: an empty selection is meaningless — the spec
    # demands a 422 (a payrun must contain at least one employee).
    if not payload.employee_ids:
        raise ValidationException("A payrun must include at least one employee.")

    employees = {
        e.id: e
        for e in db.scalars(select(Employee).where(Employee.id.in_(payload.employee_ids))).all()
    }
    missing = [eid for eid in payload.employee_ids if eid not in employees]
    if missing:
        raise ValidationException(
            "Unknown employee id(s): " + ", ".join(str(m) for m in missing) + "."
        )

    inactive = [eid for eid, e in employees.items() if e.status != EmployeeStatus.active]
    # Race-condition edge case (slow demo): an employee who went inactive
    # between wizard Step 1 and Step 2 is rejected by NAME so the UI can tell
    # them exactly who to deselect (not a silent partial failure).
    if inactive:
        raise ValidationException(
            "Employee(s) are no longer active: "
            + ", ".join(str(m) for m in inactive)
            + ". Refresh the selection and retry."
        )

    # Tampered-frontend defense: every submitted id must still match the
    # original scope filters (department + employee type), not just exist.
    mismatch = []
    for eid, e in employees.items():
        if (
            scope.department_filter_id is not None
            and e.department_id != scope.department_filter_id
        ):
            mismatch.append(eid)
        elif (
            scope.employee_type_filter is not None
            and e.employee_type != scope.employee_type_filter
        ):
            mismatch.append(eid)
    if mismatch:
        raise ValidationException(
            "Employee(s) no longer match the payrun scope: "
            + ", ".join(str(m) for m in mismatch)
            + "."
        )

    # Create the Payrun + its explicit payrun_employees selection in ONE
    # transaction (flush gives us payrun.id for the junction rows). Status is
    # always draft; created_by_user_id stamps the actor for audit (§4.6).
    payrun = Payrun(
        name=scope.name or _default_payrun_name(scope.period_start),
        salary_structure_id=scope.salary_structure_id,
        period_start=scope.period_start,
        period_end=scope.period_end,
        department_filter_id=scope.department_filter_id,
        employee_type_filter=scope.employee_type_filter,
        status=PayrunStatus.draft,
        created_by_user_id=current_user.id,
    )
    db.add(payrun)
    db.flush()
    for eid in payload.employee_ids:
        db.add(PayrunEmployee(payrun_id=payrun.id, employee_id=eid))
    db.commit()
    db.refresh(payrun)
    return PayrunRead(
        id=payrun.id,
        name=payrun.name,
        salary_structure_id=payrun.salary_structure_id,
        period_start=payrun.period_start,
        period_end=payrun.period_end,
        department_filter_id=payrun.department_filter_id,
        employee_type_filter=payrun.employee_type_filter,
        status=payrun.status,
        created_by_user_id=payrun.created_by_user_id,
        version_id=payrun.version_id,
        created_at=payrun.created_at,
        updated_at=payrun.updated_at,
        payslips=[],
    )


def get_payrun_or_404(db: Session, payrun_id: int) -> Payrun:
    """Shared lookup for every lifecycle action + detail read; 404 friendly."""
    payrun = db.get(Payrun, payrun_id)
    if payrun is None:
        raise NotFoundException(f"Payrun {payrun_id} not found.")
    return payrun


def list_payruns(
    db: Session,
    page: int = 1,
    page_size: int = 20,
    status: PayrunStatus | None = None,
    period_start: date | None = None,
    period_end: date | None = None,
    department_filter_id: int | None = None,
) -> Page[PayrunSummary]:
    """List payruns with live employee_count + payslip_count via correlated
    subqueries (no N+1). Filters compose: status AND period-overlap AND the
    department the run was scoped to."""
    emp_count = (
        select(func.count())
        .select_from(PayrunEmployee)
        .where(PayrunEmployee.payrun_id == Payrun.id)
        .scalar_subquery()
    )
    payslip_count = (
        select(func.count())
        .select_from(Payslip)
        .where(Payslip.payrun_id == Payrun.id)
        .scalar_subquery()
    )
    stmt = (
        select(Payrun, emp_count.label("employee_count"), payslip_count.label("payslip_count"))
        .order_by(Payrun.id.desc())
    )
    if status is not None:
        stmt = stmt.where(Payrun.status == status)
    if period_start is not None:
        stmt = stmt.where(Payrun.period_end >= period_start)
    if period_end is not None:
        stmt = stmt.where(Payrun.period_start <= period_end)
    if department_filter_id is not None:
        stmt = stmt.where(Payrun.department_filter_id == department_filter_id)

    page, page_size = _clamp_page(page, page_size)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.execute(stmt.offset((page - 1) * page_size).limit(page_size)).all()
    items = [
        PayrunSummary(
            id=p.id, name=p.name, salary_structure_id=p.salary_structure_id,
            period_start=p.period_start, period_end=p.period_end,
            department_filter_id=p.department_filter_id,
            employee_type_filter=p.employee_type_filter,
            status=p.status, created_by_user_id=p.created_by_user_id,
            payslip_count=pc, employee_count=ec, created_at=p.created_at,
        )
        for p, ec, pc in rows
    ]
    return Page[PayrunSummary](
        items=items, total=total, page=page, page_size=page_size
    )


def get_payrun(db: Session, payrun_id: int) -> PayrunRead:
    """Detail read: payrun + payslip summaries (id, employee, net, status,
    warning_count) eager-loaded so the drill-down list is one query."""
    payrun = get_payrun_or_404(db, payrun_id)
    payslips = list(
        db.scalars(
            select(Payslip)
            .options(selectinload(Payslip.employee), selectinload(Payslip.warnings))
            .where(Payslip.payrun_id == payrun_id)
            .order_by(Payslip.employee_id)
        ).all()
    )
    summaries = [
        PayslipSummary(
            id=ps.id,
            employee_id=ps.employee_id,
            employee_name=ps.employee.full_name,
            net_salary=ps.net_salary,
            status=ps.status,
            warning_count=sum(1 for w in ps.warnings if not _is_sentinel(w)),
        )
        for ps in payslips
    ]
    return PayrunRead(
        id=payrun.id,
        name=payrun.name,
        salary_structure_id=payrun.salary_structure_id,
        period_start=payrun.period_start,
        period_end=payrun.period_end,
        department_filter_id=payrun.department_filter_id,
        employee_type_filter=payrun.employee_type_filter,
        status=payrun.status,
        created_by_user_id=payrun.created_by_user_id,
        version_id=payrun.version_id,
        created_at=payrun.created_at,
        updated_at=payrun.updated_at,
        payslips=summaries,
    )


# ---------------------------------------------------------------------------
# Payrun lifecycle: compute / validate / mark-paid / cancel
# ---------------------------------------------------------------------------
# State machine enforced here: draft ->(compute)-> computed ->(validate)->
# validated ->(mark_paid)-> paid; draft/computed can be cancelled. Every
# transition uses _commit_with_lock_guard so the Payrun optimistic lock
# (version_id) turns concurrent editors into a clean 409.


def _commit_with_lock_guard(db: Session) -> None:
    """Commit, translating the Payrun optimistic-lock violation into a 409.

    Why optimistic locking: two HR users could click Compute/Validate at the
    same time. Eldo's schema puts version_id on Payrun/Payslip; SQLAlchemy
    auto-increments it on UPDATE and raises StaleDataError if the row changed
    underneath this session — we rollback and tell the user to refresh instead
    of silently clobbering the other user's state (prompt §3.3 concurrency)."""
    try:
        db.commit()
    except StaleDataError:
        db.rollback()
        raise ConflictException(
            "Payrun was modified concurrently — please refresh and retry."
        )


def _overlapping_payslips(db: Session, employee_id: int, payrun: Payrun) -> list[Payslip]:
    """Other payslips (across payruns) whose period overlaps this payrun.
    Used by compute_payrun to attach an `overlapping_period` warning to BOTH
    payslips when an employee is caught in two overlapping payruns. Uses
    Eldo's ix_payslips_employee_period composite index. Excludes cancelled
    payslips (they don't count as real coverage)."""
    return list(
        db.scalars(
            select(Payslip).where(
                Payslip.employee_id == employee_id,
                Payslip.payrun_id != payrun.id,
                Payslip.period_start <= payrun.period_end,
                Payslip.period_end >= payrun.period_start,
                Payslip.status != PayrunStatus.cancelled,
            )
        ).all()
    )


def compute_payrun(db: Session, payrun_id: int) -> ComputeResult:
    """Idempotent Compute: creates/replaces Payslip + lines + warnings for
    every payrun_employee; skips finalized payslips and reports them.
    Re-running replaces lines rather than appending duplicates."""
    payrun = get_payrun_or_404(db, payrun_id)
    # A cancelled run is dead; a paid run is a historical record. Both are
    # rejected up front so the engine can never rewrite finalized money.
    if payrun.status == PayrunStatus.cancelled:
        raise ConflictException("A cancelled payrun cannot be computed.")
    if payrun.status == PayrunStatus.paid:
        raise ConflictException("A paid payrun cannot be recomputed.")

    # Load the EXPLICIT employee selection made in wizard Step 2 (the
    # payrun_employees junction), joined to Employee for names/contracts.
    members = list(
        db.scalars(
            select(Employee)
            .join(PayrunEmployee, PayrunEmployee.employee_id == Employee.id)
            .where(PayrunEmployee.payrun_id == payrun_id)
            .order_by(Employee.id)
        ).all()
    )
    if not members:
        raise ValidationException("Payrun has no employees selected.")

    # Map employee_id -> existing Payslip (if this is a RE-compute) so we can
    # decide per employee: replace (draft/computed) vs skip (validated/paid).
    existing = {
        ps.employee_id: ps
        for ps in db.scalars(
            select(Payslip).where(Payslip.payrun_id == payrun_id)
        ).all()
    }

    skipped: list[ComputeSkippedItem] = []
    warnings_added = 0
    processed = 0

    for emp in members:
        # Prompt §3.3 partial-recompute rule: finalized payslips are NEVER
        # touched (they're historical/legal records). We skip them and report
        # WHY in the ComputeResult so HR isn't surprised by the counts.
        payslip = existing.get(emp.id)
        if payslip is not None and payslip.status in (
            PayrunStatus.validated,
            PayrunStatus.paid,
        ):
            skipped.append(
                ComputeSkippedItem(
                    payslip_id=payslip.id,
                    employee_name=emp.full_name,
                    reason=f"payslip is already {payslip.status.value}",
                )
            )
            continue

        # Run the pure engine (read-only queries inside). A structural failure
        # (e.g. missing salary structure) is a hard 409; per-rule problems
        # come back as warnings inside `computed` instead.
        try:
            computed = compute_payslip_for_employee(db, payrun, emp)
        except PayrollEngineError as exc:
            raise ConflictException(str(exc))

        if payslip is None:
            # First compute: create the Payslip row (flush to get its id so
            # we can attach lines/warnings with the FK).
            payslip = Payslip(
                payrun_id=payrun_id,
                employee_id=emp.id,
                period_start=payrun.period_start,
                period_end=payrun.period_end,
            )
            db.add(payslip)
            db.flush()  # need payslip.id for lines
        else:
            # Re-compute on a draft/computed payslip: REPLACE lines+warnings
            # (delete-then-reinsert) — never append duplicates (idempotency,
            # prompt §3.3). Deleting the old lines/warnings here and re-adding
            # below is what keeps re-runs clean.
            payslip.lines.clear()
            payslip.warnings.clear()

        payslip.contract_id = computed.contract_id
        payslip.period_start = payrun.period_start
        payslip.period_end = payrun.period_end
        payslip.worked_days = computed.worked_days
        payslip.gross_salary = computed.gross_salary
        payslip.net_salary = computed.net_salary
        payslip.status = PayrunStatus.computed

        for line in computed.lines:
            payslip.lines.append(
                PayslipLine(
                    salary_rule_id=line.salary_rule_id,
                    sequence=line.sequence,
                    code=line.code,
                    name=line.name,
                    category=line.category,
                    amount=line.amount,
                )
            )
        for wtype, message in computed.warnings:
            payslip.warnings.append(PayslipWarning(warning_type=wtype, message=message))
            warnings_added += 1

        # Cross-domain warnings (read-only into Ameen's/Ambuj's data):
        # (1) missing bank details — employee has no employee_bank_details row
        # (Ameen's domain; 1:1 table). Not blocking for Validate, blocks send.
        if emp.bank_detail is None:
            payslip.warnings.append(
                PayslipWarning(
                    warning_type=PayslipWarningType.missing_bank_details,
                    message="No bank details on file — payout will be blocked "
                    "until added.",
                )
            )
            warnings_added += 1

        # (2) overlapping-period — same employee appears in another payrun
        # whose period overlaps this one (cross-payrun duplicate coverage).
        # Within one payrun duplicates are structurally impossible (UNIQUE
        # payrun_id+employee_id), so this is purely the cross-payrun case.
        for other in _overlapping_payslips(db, emp.id, payrun):
            payslip.warnings.append(
                PayslipWarning(
                    warning_type=PayslipWarningType.overlapping_period,
                    message=f"Employee appears in overlapping payrun {other.payrun_id} "
                    f"({other.period_start} to {other.period_end}).",
                )
            )
            warnings_added += 1
            # Mirror the warning on the other payslip — only if it is still
            # mutable (finalized ones are historical records, never touched).
            if other.status in (PayrunStatus.draft, PayrunStatus.computed):
                other.warnings.append(
                    PayslipWarning(
                        warning_type=PayslipWarningType.overlapping_period,
                        message=f"Employee appears in overlapping payrun "
                        f"{payrun_id} ({payrun.period_start} to {payrun.period_end}).",
                    )
                )

        processed += 1

    # Only after every employee succeeded do we flip the payrun to `computed`
    # and commit — the optimistic-lock guard makes concurrent computes 409.
    payrun.status = PayrunStatus.computed
    _commit_with_lock_guard(db)
    db.refresh(payrun)

    return ComputeResult(
        payrun_id=payrun_id,
        status=payrun.status,
        payslips_computed=processed,
        payslips_skipped=skipped,
        warnings_added=warnings_added,
    )


def validate_payrun(db: Session, payrun_id: int) -> ValidateResult:
    """Validate: only from `computed`, and only when no BLOCKING warning is
    open (negative_net / missing_contract). missing_bank_details does NOT
    block validation (it blocks sending later)."""
    payrun = get_payrun_or_404(db, payrun_id)
    # Guard rails: a cancelled run is dead, a paid run is history, and a
    # never-computed draft has no numbers to validate — all 409.
    if payrun.status == PayrunStatus.cancelled:
        raise ConflictException("A cancelled payrun cannot be validated.")
    if payrun.status == PayrunStatus.paid:
        raise ConflictException("A paid payrun cannot be re-validated.")
    if payrun.status != PayrunStatus.computed:
        raise ConflictException(
            "Payrun must be computed before it can be validated (current "
            f"status: {payrun.status.value})."
        )

    payslips = list(
        db.scalars(
            select(Payslip)
            .options(selectinload(Payslip.employee), selectinload(Payslip.warnings))
            .where(
                Payslip.payrun_id == payrun_id,
                Payslip.status != PayrunStatus.cancelled,
            )
        ).all()
    )
    # Collect every BLOCKING warning across the run's payslips (employee name
    # + type + message so the error is actionable). Sentinel markers excluded.
    # If ANY exist we refuse to validate — signing off amounts that are zeroed
    # or negative would be a payroll error the demo can't explain away.
    blocking = [
        f"{ps.employee.full_name}: [{w.warning_type.value}] {w.message}"
        for ps in payslips
        for w in ps.warnings
        if w.warning_type in BLOCKING_WARNING_TYPES and not _is_sentinel(w)
    ]
    if blocking:
        raise ConflictException(
            "Cannot validate — unresolved blocking warning(s): "
            + "; ".join(blocking)
            + ". Resolve them (or recompute) before validating."
        )

    for ps in payslips:
        ps.status = PayrunStatus.validated
    payrun.status = PayrunStatus.validated
    _commit_with_lock_guard(db)
    db.refresh(payrun)
    return ValidateResult(
        payrun_id=payrun_id, status=payrun.status, validated_payslips=len(payslips)
    )


def mark_paid(db: Session, payrun_id: int) -> MarkPaidResult:
    """Mark Paid: only from `validated`; idempotency guard -> 409 on a
    second call (no duplicate state change)."""
    payrun = get_payrun_or_404(db, payrun_id)
    # Idempotency (prompt §3.3): calling twice on an already-paid run is a
    # 409, not a silent no-op — a paid run is a historical record. And only
    # validated runs may be paid (a raw computed run hasn't been signed off).
    if payrun.status == PayrunStatus.paid:
        raise ConflictException("Payrun is already marked as paid.")
    if payrun.status != PayrunStatus.validated:
        raise ConflictException(
            "Payrun must be validated before it can be marked paid (current "
            f"status: {payrun.status.value})."
        )

    payslips = list(
        db.scalars(
            select(Payslip).where(
                Payslip.payrun_id == payrun_id,
                Payslip.status.in_([PayrunStatus.computed, PayrunStatus.validated]),
            )
        ).all()
    )
    for ps in payslips:
        ps.status = PayrunStatus.paid
    payrun.status = PayrunStatus.paid
    _commit_with_lock_guard(db)
    db.refresh(payrun)
    return MarkPaidResult(
        payrun_id=payrun_id, status=payrun.status, paid_payslips=len(payslips)
    )


def cancel_payrun(db: Session, payrun_id: int) -> CancelResult:
    """Cancel: only from draft/computed. Validated/paid runs are historical
    records and can never be cancelled."""
    payrun = get_payrun_or_404(db, payrun_id)
    # Spec edge case: never cancel a validated/paid run — that's a historical
    # record now (soft-delete/history philosophy, arch §4.5). Draft/computed
    # runs are still work-in-progress and may be abandoned.
    if payrun.status not in (PayrunStatus.draft, PayrunStatus.computed):
        raise ConflictException(
            "Only draft or computed payruns can be cancelled (current "
            f"status: {payrun.status.value})."
        )
    payslips = list(
        db.scalars(
            select(Payslip).where(
                Payslip.payrun_id == payrun_id,
                Payslip.status != PayrunStatus.cancelled,
            )
        ).all()
    )
    for ps in payslips:
        ps.status = PayrunStatus.cancelled
    payrun.status = PayrunStatus.cancelled
    _commit_with_lock_guard(db)
    db.refresh(payrun)
    return CancelResult(
        payrun_id=payrun_id, status=payrun.status, cancelled_payslips=len(payslips)
    )


# ---------------------------------------------------------------------------
# Payslips: list / get / me / pdf / bulk email
# ---------------------------------------------------------------------------
# Payslip = one employee's computed result in one payrun. Reads eager-load
# employee/lines/warnings (selectinload) so breakdown views never N+1.


def get_payslip_or_404(db: Session, payslip_id: int) -> Payslip:
    """Detail lookup with eager-loaded relations; 404 friendly. Used by
    get_payslip + get_payslip_pdf + can_access_payslip checks."""
    payslip = db.scalar(
        select(Payslip)
        .options(
            selectinload(Payslip.employee),
            selectinload(Payslip.lines),
            selectinload(Payslip.warnings),
        )
        .where(Payslip.id == payslip_id)
    )
    if payslip is None:
        raise NotFoundException(f"Payslip {payslip_id} not found.")
    return payslip


def list_payslips(
    db: Session,
    page: int = 1,
    page_size: int = 20,
    payrun_id: int | None = None,
    employee_id: int | None = None,
    status: PayrunStatus | None = None,
) -> Page[PayslipSummaryItem]:
    """Payslip list with composable filters. ALSO the backend of
    get_my_payslips (employee_id = current user's employee id) — one query
    shape serves both the payroll screens and the EMPLOYEE self-service view."""
    stmt = (
        select(Payslip)
        .options(selectinload(Payslip.employee), selectinload(Payslip.warnings))
        .order_by(Payslip.id.desc())
    )
    if payrun_id is not None:
        stmt = stmt.where(Payslip.payrun_id == payrun_id)
    if employee_id is not None:
        stmt = stmt.where(Payslip.employee_id == employee_id)
    if status is not None:
        stmt = stmt.where(Payslip.status == status)
    items, total, page, page_size = _paginate(db, stmt, page, page_size)
    return Page[PayslipSummaryItem](
        items=[
            PayslipSummaryItem(
                id=ps.id,
                payrun_id=ps.payrun_id,
                employee_id=ps.employee_id,
                employee_name=ps.employee.full_name,
                period_start=ps.period_start,
                period_end=ps.period_end,
                gross_salary=ps.gross_salary,
                net_salary=ps.net_salary,
                status=ps.status,
                warning_count=sum(1 for w in ps.warnings if not _is_sentinel(w)),
            )
            for ps in items
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


def get_payslip(db: Session, payslip_id: int) -> PayslipRead:
    """Full breakdown read (lines + warnings) for GET /payslips/{id}."""
    payslip = get_payslip_or_404(db, payslip_id)
    return _payslip_to_read(payslip)


def _payslip_to_read(payslip: Payslip) -> PayslipRead:
    """Shared ORM->DTO mapper (used by get_payslip). Filters the internal
    SENT_AT sentinels out of the warnings list so the API never exposes the
    email-idempotency hack as a real payroll warning."""
    warnings = [
        PayslipWarningRead.model_validate(w)
        for w in payslip.warnings
        if not _is_sentinel(w)
    ]
    return PayslipRead(
        id=payslip.id,
        payrun_id=payslip.payrun_id,
        employee_id=payslip.employee_id,
        employee_name=payslip.employee.full_name,
        contract_id=payslip.contract_id,
        period_start=payslip.period_start,
        period_end=payslip.period_end,
        worked_days=payslip.worked_days,
        gross_salary=payslip.gross_salary,
        net_salary=payslip.net_salary,
        status=payslip.status,
        version_id=payslip.version_id,
        created_at=payslip.created_at,
        updated_at=payslip.updated_at,
        lines=[PayslipLineRead.model_validate(l) for l in payslip.lines],
        warnings=warnings,
    )


def get_my_payslips(
    db: Session, user: User, page: int = 1, page_size: int = 20
) -> Page[PayslipSummaryItem]:
    """Current employee's own payslips (EMPLOYEE self-service).
    A user with no linked employee row (e.g. ADMIN) gets 404 — they have no
    payslips "of their own" and should use the payroll-role routes instead."""
    if user.employee is None:
        raise NotFoundException("No employee is linked to this account.")
    return list_payslips(
        db, page=page, page_size=page_size, employee_id=user.employee_id
    )


def can_access_payslip(user: User, payslip: Payslip) -> bool:
    """Payroll roles see everything; EMPLOYEE only their own payslip.
    The enforcement half of the RBAC boundary leak edge case (arch §5.8): an
    EMPLOYEE hitting /payslips/{other_id}/pdf must 403, never leak data."""
    if _user_has_payroll_role(user):
        return True
    return user.employee is not None and user.employee.id == payslip.employee_id


def get_payslip_pdf(db: Session, payslip_id: int, user: User) -> tuple[bytes, str]:
    """Streams a generated PDF. EMPLOYEE role may only fetch their own
    (403 otherwise); HR_MANAGER has no payroll access at all.

    Called by GET /payslips/{id}/pdf (with the real user) AND by
    send_payslips (with _system_user() to bypass the per-user ownership
    check — the batch endpoint is already RBAC-gated)."""
    payslip = get_payslip_or_404(db, payslip_id)
    if not can_access_payslip(user, payslip):
        raise ForbiddenException(
            "You do not have permission to view this payslip."
        )

    payrun = db.get(Payrun, payslip.payrun_id)
    employee = payslip.employee
    department = db.get(Department, employee.department_id) if employee.department_id else None
    structure = db.get(SalaryStructure, payrun.salary_structure_id)
    company = (
        db.get(Company, structure.company_id)
        if structure and structure.company_id is not None
        else None
    )

    lines = [
        (l.sequence, l.code, l.name, l.category.value, l.amount)
        for l in payslip.lines
    ]
    warnings = [
        (w.warning_type.value, w.message)
        for w in payslip.warnings
        if not _is_sentinel(w)
    ]
    pdf_bytes = pdf_mod.render_payslip_pdf(
        company_name=company.name if company else "PeoplePay360",
        payrun_name=payrun.name,
        period_start=payslip.period_start,
        period_end=payslip.period_end,
        employee_name=employee.full_name,
        employee_email=employee.work_email,
        employee_type=employee.employee_type.value,
        department_name=department.name if department else None,
        worked_days=payslip.worked_days,
        gross_salary=payslip.gross_salary,
        net_salary=payslip.net_salary,
        status=payslip.status,
        lines=lines,
        warnings=warnings,
    )
    return pdf_bytes, f"payslip_{payslip_id}.pdf"


def send_payslips(db: Session, payrun_id: int) -> SendPayslipsResult:
    """Bulk email every payslip in the payrun; per-employee results, never
    all-or-nothing. Idempotent via the SENT_AT sentinel — clicking twice does
    not double-send. Only validated/paid payruns are sent (a DRAFT-watermarked
    PDF must not be emailed as final)."""
    payrun = get_payrun_or_404(db, payrun_id)
    # A draft/computed payslip PDF carries the DRAFT watermark — emailing it
    # as if final would be a demo disaster, so only validated/paid runs send.
    if payrun.status not in (PayrunStatus.validated, PayrunStatus.paid):
        raise ConflictException(
            "Payrun must be validated (or paid) before payslips can be sent "
            f"(current status: {payrun.status.value})."
        )

    payslips = list(
        db.scalars(
            select(Payslip)
            .options(selectinload(Payslip.employee), selectinload(Payslip.warnings))
            .where(
                Payslip.payrun_id == payrun_id,
                Payslip.status != PayrunStatus.cancelled,
            )
            .order_by(Payslip.employee_id)
        ).all()
    )
    if not payslips:
        raise ConflictException("Payrun has no payslips to send — compute it first.")

    results: list[SendPayslipResultItem] = []
    sent_count = 0

    for ps in payslips:
        emp = ps.employee
        # Idempotency check FIRST: a hidden SENT_AT sentinel warning means this
        # payslip was already emailed on a previous click — skip, don't resend.
        already_sent = any(_is_sentinel(w) for w in ps.warnings)
        if already_sent:
            results.append(
                SendPayslipResultItem(
                    employee_id=emp.id,
                    employee_name=emp.full_name,
                    sent=False,
                    error="already sent previously",
                )
            )
            continue
        # Per-recipient eligibility: missing bank details or missing email are
        # per-employee SKIPS (reported in the result list), never batch-killers
        # (prompt §3.5 — one bad recipient must not abort the rest).
        if emp.bank_detail is None:
            results.append(
                SendPayslipResultItem(
                    employee_id=emp.id,
                    employee_name=emp.full_name,
                    sent=False,
                    error="missing bank details",
                )
            )
            continue
        if not emp.work_email:
            results.append(
                SendPayslipResultItem(
                    employee_id=emp.id,
                    employee_name=emp.full_name,
                    sent=False,
                    error="missing work email",
                )
            )
            continue

        try:
            # Render the PDF under the privileged system context (the router
            # already gated the whole batch to payroll roles) and email it.
            pdf_bytes, _filename = get_payslip_pdf(db, ps.id, _system_user())
            pdf_mod.send_payslip_email(
                to_email=emp.work_email,
                employee_name=emp.full_name,
                payrun_name=payrun.name,
                period_start=ps.period_start,
                period_end=ps.period_end,
                pdf_bytes=pdf_bytes,
            )
        except Exception as exc:  # one bad recipient never aborts the batch
            results.append(
                SendPayslipResultItem(
                    employee_id=emp.id,
                    employee_name=emp.full_name,
                    sent=False,
                    error=f"send failed: {exc}",
                )
            )
            continue

        # Mark sent WITHOUT a schema change: append the hidden SENT_AT sentinel
        # warning. Committed at the end with the other changes so a crash
        # mid-batch rolls the whole thing back (no half-sent state).
        ps.warnings.append(
            PayslipWarning(
                warning_type=PayslipWarningType.other,
                message=f"{SENT_AT_SENTINEL}{datetime.now(timezone.utc).isoformat()}",
            )
        )
        sent_count += 1
        results.append(
            SendPayslipResultItem(
                employee_id=emp.id, employee_name=emp.full_name, sent=True
            )
        )

    db.commit()
    return SendPayslipsResult(
        payrun_id=payrun_id,
        sent_count=sent_count,
        skipped_count=len(results) - sent_count,
        results=results,
    )


# ---------------------------------------------------------------------------
# Payroll Dashboard — read-only aggregations across all modules
# ---------------------------------------------------------------------------
#
# Department hierarchy roll-up: the seeded departments have no parents, so the
# roll-up of child totals into parents is SKIPPED for now (documented
# nice-to-have in the prompt; revisit if the org chart grows).


def _employee_scope_filter(stmt, department_id: int | None, employee_type: EmployeeType | None):
    """Shared filter-building helper: department + employee_type compose.
    One of the two helpers that let all six dashboard endpoints apply the same
    filter semantics (department AND employee_type) — never copy-pasted per
    endpoint (prompt §4: write ONE shared helper)."""
    if department_id is not None:
        stmt = stmt.where(Employee.department_id == department_id)
    if employee_type is not None:
        stmt = stmt.where(Employee.employee_type == employee_type)
    return stmt


def _period_overlap(
    stmt,
    period_start: date | None,
    period_end: date | None,
    table=None,
    start_attr=None,
    end_attr=None,
):
    """Shared period filter: rows whose period overlaps [period_start,
    period_end]. Tables with differently-named columns pass start_attr/end_attr
    (TimeOffRequest uses date_from/date_to).

    The OTHER shared dashboard helper. Overlap (start <= end_filter AND end
    >= start_filter), inclusive both ends — matches engine contract semantics
    and lets every dashboard query filter by period uniformly."""
    table = table or Payslip
    start_col = start_attr or table.period_start
    end_col = end_attr or table.period_end
    if period_start is not None:
        stmt = stmt.where(end_col >= period_start)
    if period_end is not None:
        stmt = stmt.where(start_col <= period_end)
    return stmt


def _filtered_employee_ids(
    db: Session, department_id: int | None, employee_type: EmployeeType | None
) -> list[int]:
    stmt = select(Employee.id)
    stmt = _employee_scope_filter(stmt, department_id, employee_type)
    return list(db.scalars(stmt).all())


def _expected_days_by_schedule(
    db: Session, period_start: date, period_end: date
) -> dict[int, int]:
    """Map working_schedule_id -> expected working days in the period.
    Iterates the actual dates and counts weekdays listed in each schedule's
    lines (READ-ONLY into Ameen's WorkingSchedule). Feeds both attendance
    health % and the attendance-overview absent computation — absence is
    DERIVED here (schedule-expected minus attended) because Ambuj's
    attendances table stores no synthetic absent rows."""
    schedules = list(
        db.scalars(
            select(WorkingSchedule).options(selectinload(WorkingSchedule.lines))
        ).all()
    )
    out: dict[int, int] = {}
    for sched in schedules:
        dow_set = {line.day_of_week for line in sched.lines}
        span = (period_end - period_start).days + 1
        out[sched.id] = sum(
            1
            for i in range(span)
            if (period_start + timedelta(days=i)).weekday() in dow_set
        )
    return out


def get_kpis(
    db: Session,
    period_start: date | None,
    period_end: date | None,
    department_id: int | None,
    employee_type: EmployeeType | None,
) -> KpisResponse:
    """GET /dashboard/kpis — the headline cards. Four aggregate queries over
    paid/generated payslips + approved time-off + attendance health, all
    scoped by the shared employee + period filters."""
    emp_ids = _filtered_employee_ids(db, department_id, employee_type)
    # Resolve the filtered employee set ONCE, then reuse it as an IN-clause
    # on every sub-query (consistent scoping across all KPI numbers). An
    # empty filter result becomes IN([]) = matches nothing, not everything.
    emp_filter = Employee.id.in_(emp_ids) if emp_ids else Employee.id.in_([])

    # total_net_salary_paid — PAID payslips ONLY. The prompt calls this out
    # as THE demo-breaking bug: if draft/computed amounts leak in, the "Paid"
    # KPI shows money the company never disbursed. Filter status='paid' here.
    paid_stmt = (
        select(func.coalesce(func.sum(Payslip.net_salary), 0))
        .join(Employee, Employee.id == Payslip.employee_id)
        .where(Payslip.status == PayrunStatus.paid)
    )
    paid_stmt = _period_overlap(paid_stmt, period_start, period_end)
    paid_stmt = paid_stmt.where(emp_filter)
    total_paid = Decimal(db.scalar(paid_stmt) or 0)

    # payslips_generated — any real payslip (draft/computed/validated/paid),
    # cancelled excluded (a cancelled payslip never 'generated' an amount).
    gen_stmt = (
        select(func.count(Payslip.id))
        .join(Employee, Employee.id == Payslip.employee_id)
        .where(Payslip.status != PayrunStatus.cancelled)
    )
    gen_stmt = _period_overlap(gen_stmt, period_start, period_end)
    gen_stmt = gen_stmt.where(emp_filter)
    payslips_generated = db.scalar(gen_stmt) or 0

    # average_salary — mean net over computed/validated/paid (draft rows are
    # placeholders, not real amounts yet). avg() returns None on zero rows;
    # Decimal(None or 0) guards the divide-by-zero/empty case (prompt §4).
    avg_stmt = (
        select(func.avg(Payslip.net_salary))
        .join(Employee, Employee.id == Payslip.employee_id)
        .where(Payslip.status.in_(
            [PayrunStatus.computed, PayrunStatus.validated, PayrunStatus.paid]
        ))
    )
    avg_stmt = _period_overlap(avg_stmt, period_start, period_end)
    avg_stmt = avg_stmt.where(emp_filter)
    avg_value = db.scalar(avg_stmt)
    average_salary = Decimal(avg_value or 0)  # guard: no rows -> 0, not a 500

    # approved time-off days: APPROVED requests, day-unit types ONLY (hour-
    # unit types can't sum into a 'days' KPI), overlapping the period. Reads
    # Ambuj's TimeOffRequest + TimeOffType (READ-ONLY).
    toff_stmt = (
        select(func.coalesce(func.sum(TimeOffRequest.duration), 0))
        .join(Employee, Employee.id == TimeOffRequest.employee_id)
        .join(TimeOffType, TimeOffType.id == TimeOffRequest.time_off_type_id)
        .where(TimeOffRequest.status == "approved", TimeOffType.unit == "days")
    )
    toff_stmt = _period_overlap(
        toff_stmt, period_start, period_end, table=TimeOffRequest,
        start_attr=TimeOffRequest.date_from, end_attr=TimeOffRequest.date_to,
    )
    toff_stmt = toff_stmt.where(emp_filter)
    approved_toff_days = Decimal(db.scalar(toff_stmt) or 0)

    attendance_health_pct = _attendance_health(
        db, period_start, period_end, department_id, employee_type
    )

    return KpisResponse(
        total_net_salary_paid=total_paid,
        payslips_generated=payslips_generated,
        average_salary=average_salary,
        approved_time_off_days=approved_toff_days,
        attendance_health_pct=attendance_health_pct,
    )


def _resolve_attendance_period(
    period_start: date | None, period_end: date | None
) -> tuple[date, date]:
    """Attendance-derived metrics need an explicit window; when the caller
    omits it we default to the current calendar month so the endpoints stay
    useful without filters.
    Why an explicit window at all: attendance rows are timestamps, so "no
    filter" can't mean "all time" — that would count every historical row.
    Defaulting to the current month is the dashboard-friendly interpretation."""
    today = date.today()
    return (
        period_start or today.replace(day=1),
        period_end or today,
    )


def _attendance_health(
    db: Session,
    period_start: date | None,
    period_end: date | None,
    department_id: int | None,
    employee_type: EmployeeType | None,
) -> float:
    """present_and_ontime / total_expected_days over the filtered period.
    Helper for get_kpis. Denominator = sum of each employee's schedule-
    expected working days (so weekends never count against health); guarded
    -> 0.0 when there are no employees or no expected days."""
    period_start, period_end = _resolve_attendance_period(period_start, period_end)
    emp_ids = _filtered_employee_ids(db, department_id, employee_type)
    if not emp_ids:
        return 0.0

    present_count = db.scalar(
        select(func.count(Attendance.id)).where(
            Attendance.employee_id.in_(emp_ids),
            Attendance.status == AttendanceStatus.present,
            func.date(Attendance.check_in) >= period_start,
            func.date(Attendance.check_in) <= period_end,
        )
    ) or 0

    employees = list(
        db.scalars(select(Employee).where(Employee.id.in_(emp_ids))).all()
    )
    expected_map = _expected_days_by_schedule(db, period_start, period_end)
    total_expected = sum(
        expected_map.get(e.working_schedule_id, 0) for e in employees
    )
    if total_expected == 0:
        return 0.0
    return round((present_count / total_expected) * 100.0, 2)


def get_salary_by_department(
    db: Session,
    period_start: date | None,
    period_end: date | None,
    department_id: int | None,
    employee_type: EmployeeType | None,
) -> list[SalaryByDepartmentItem]:
    """{department_name, total_salary (paid net), headcount} — bar chart data.
    Two grouped queries merged in Python: headcount counts ACTIVE employees
    per department (from Ameen's employees), total_salary sums PAID net from
    payslips scoped by period + filters (department roll-up into parent depts
    is SKIPPED — seeded org has no parents, documented above)."""
    emp_stmt = (
        select(
            Department.id,
            Department.name,
            func.count(Employee.id).label("headcount"),
        )
        .join(Employee, Employee.department_id == Department.id)
        .where(Employee.status == EmployeeStatus.active)
        .group_by(Department.id, Department.name)
        .order_by(Department.name)
    )
    emp_stmt = _employee_scope_filter(emp_stmt, department_id, employee_type)
    headcount_rows = db.execute(emp_stmt).all()

    payslip_stmt = (
        select(
            Department.id,
            Department.name,
            func.coalesce(func.sum(Payslip.net_salary), 0).label("total"),
        )
        .join(Employee, Employee.id == Payslip.employee_id)
        .join(Department, Department.id == Employee.department_id)
        .where(Payslip.status == PayrunStatus.paid)
        .group_by(Department.id, Department.name)
    )
    payslip_stmt = _period_overlap(payslip_stmt, period_start, period_end)
    payslip_stmt = _employee_scope_filter(payslip_stmt, department_id, employee_type)
    salary_rows = db.execute(payslip_stmt).all()

    totals = {dept_id: total for dept_id, _name, total in salary_rows}
    return [
        SalaryByDepartmentItem(
            department_name=name,
            total_salary=Decimal(totals.get(dept_id, 0)),
            headcount=headcount,
        )
        for dept_id, name, headcount in headcount_rows
    ]


def get_monthly_net_salary_trend(
    db: Session,
    months: int,
    period_start: date | None,
    period_end: date | None,
    department_id: int | None,
    employee_type: EmployeeType | None,
) -> list[MonthlyTrendItem]:
    """Line chart: last N months of PAID payslips (months with no data -> 0).
    Groups PAID payslips by month of period_end (date_trunc), then builds the
    full N-month window back from the anchor month so every bucket exists —
    missing months render as 0, never as gaps in the chart."""
    emp_filter = Employee.id.in_(
        _filtered_employee_ids(db, department_id, employee_type) or [-1]
    )
    stmt = (
        select(
            func.date_trunc("month", Payslip.period_end).label("month"),
            func.coalesce(func.sum(Payslip.net_salary), 0).label("total"),
        )
        .join(Employee, Employee.id == Payslip.employee_id)
        .where(Payslip.status == PayrunStatus.paid, emp_filter)
    )
    stmt = _period_overlap(stmt, period_start, period_end)
    stmt = stmt.group_by("month").order_by("month")
    rows = db.execute(stmt).all()
    totals = {row.month.date().replace(day=1): Decimal(row.total) for row in rows}

    anchor = (period_end or date.today()).replace(day=1)
    out: list[MonthlyTrendItem] = []
    for i in range(months - 1, -1, -1):
        month = _add_months(anchor, -i)
        out.append(
            MonthlyTrendItem(
                month=month.strftime("%Y-%m"),
                total_net_salary=totals.get(month, Decimal("0")),
            )
        )
    return out


def _add_months(d: date, delta: int) -> date:
    """Calendar-accurate month arithmetic (31-day stepping skips short
    months). Returns the first day of the shifted month.
    Why hand-rolled: timedelta(days=31) drift would mislabel months; this
    pure integer month-index math (year*12 + month + delta) is exact for
    any delta and clamps to the first of the shifted month."""
    month_index = d.year * 12 + (d.month - 1) + delta
    year, month = divmod(month_index, 12)
    return date(year, month + 1, 1)


def get_attendance_overview(
    db: Session,
    period_start: date | None,
    period_end: date | None,
    department_id: int | None,
    employee_type: EmployeeType | None,
) -> AttendanceOverview:
    """Counts by status + computed absent/coverage (absent = expected days
    minus attended days — Ambuj's table has no synthetic absent rows).
    Defaults to the current calendar month when no period is given.

    present/late/overtime/missing_checkouts/manual_edits are direct counts
    over Ambuj's attendances (his statuses + is_manual_correction flag);
    absent and coverage_pct are DERIVED here from schedule expectations."""
    period_start, period_end = _resolve_attendance_period(period_start, period_end)
    emp_ids = _filtered_employee_ids(db, department_id, employee_type)
    if not emp_ids:
        return AttendanceOverview(present=0, late=0, absent=0, overtime=0,
                                  missing_checkouts=0, manual_edits=0, coverage_pct=0.0)

    def _count(status=None, manual_only=False):
        stmt = select(func.count(Attendance.id)).where(
            Attendance.employee_id.in_(emp_ids),
            func.date(Attendance.check_in) >= period_start,
            func.date(Attendance.check_in) <= period_end,
        )
        if status is not None:
            stmt = stmt.where(Attendance.status == status)
        if manual_only:
            stmt = stmt.where(Attendance.is_manual_correction.is_(True))
        return db.scalar(stmt) or 0

    present = _count(AttendanceStatus.present)
    late = _count(AttendanceStatus.late)
    overtime = _count(AttendanceStatus.overtime)
    missing_checkouts = _count(AttendanceStatus.missing_checkout)
    manual_edits = _count(manual_only=True)

    attended = db.scalar(
        select(func.count(func.distinct(func.date(Attendance.check_in)))).where(
            Attendance.employee_id.in_(emp_ids),
            Attendance.status != AttendanceStatus.absent,
            func.date(Attendance.check_in) >= period_start,
            func.date(Attendance.check_in) <= period_end,
        )
    ) or 0

    employees = list(
        db.scalars(select(Employee).where(Employee.id.in_(emp_ids))).all()
    )
    expected_map = _expected_days_by_schedule(db, period_start, period_end)
    expected = sum(expected_map.get(e.working_schedule_id, 0) for e in employees)
    absent = max(0, expected - attended)
    coverage_pct = round((attended / expected) * 100.0, 2) if expected else 0.0

    return AttendanceOverview(
        present=present, late=late, absent=absent, overtime=overtime,
        missing_checkouts=missing_checkouts, manual_edits=manual_edits,
        coverage_pct=coverage_pct,
    )


def get_time_off_overview(
    db: Session,
    period_start: date | None,
    period_end: date | None,
    department_id: int | None,
    employee_type: EmployeeType | None,
) -> TimeOffOverview:
    """GET /dashboard/time-off-overview. approved days = SUM over approved
    day-unit requests; pending = count of to_approve; balances_by_type reads
    the LIVE v_time_off_balances view (Eldo's SQL view, allocated - taken)
    aggregated across the filtered employees — balances are never stored."""
    emp_filter = Employee.id.in_(
        _filtered_employee_ids(db, department_id, employee_type) or [-1]
    )

    approved = _timeoff_aggregate(
        db, "approved", period_start, period_end, emp_filter, aggregate="sum"
    )
    pending = _timeoff_aggregate(
        db, "to_approve", period_start, period_end, emp_filter, aggregate="count"
    )

    # balances by type (live view, aggregated across the filtered employees)
    bal_rows = db.execute(
        select(
            TimeOffType.name.label("type_name"),
            func.coalesce(func.sum(TimeOffBalanceView.remaining), 0).label("remaining"),
        )
        .join(TimeOffType, TimeOffType.id == TimeOffBalanceView.time_off_type_id)
        .join(Employee, Employee.id == TimeOffBalanceView.employee_id)
        .where(emp_filter)
        .group_by(TimeOffType.name)
        .order_by(TimeOffType.name)
    ).all()

    return TimeOffOverview(
        approved_days=Decimal(approved or 0),
        pending_requests=int(pending or 0),
        balances_by_type=[
            TimeOffBalanceItem(time_off_type_name=name, remaining=Decimal(remaining))
            for name, remaining in bal_rows
        ],
    )


def _timeoff_aggregate(
    db: Session,
    status: str,
    period_start: date | None,
    period_end: date | None,
    emp_filter,
    aggregate: str,
):
    """Shared helper: approved-days sum / pending count for filtered employees,
    with period overlap (day-unit types only for the days sum).
    One helper serves both numbers in get_time_off_overview — sum(duration)
    for 'approved', count(rows) for 'to_approve' — so the filtering logic
    (status + employee set + period overlap + day-unit join) lives once."""
    if aggregate == "sum":
        expr = func.coalesce(func.sum(TimeOffRequest.duration), 0)
    else:
        expr = func.count(TimeOffRequest.id)
    stmt = (
        select(expr)
        .join(Employee, Employee.id == TimeOffRequest.employee_id)
        .where(TimeOffRequest.status == status, emp_filter)
    )
    if status == "approved":
        stmt = stmt.join(TimeOffType, TimeOffType.id == TimeOffRequest.time_off_type_id)
        stmt = stmt.where(TimeOffType.unit == "days")
    if period_start is not None:
        stmt = stmt.where(TimeOffRequest.date_to >= period_start)
    if period_end is not None:
        stmt = stmt.where(TimeOffRequest.date_from <= period_end)
    return db.scalar(stmt)


def get_payroll_alerts(
    db: Session,
    period_start: date | None,
    period_end: date | None,
    department_id: int | None,
    employee_type: EmployeeType | None,
) -> PayrollAlertsResponse:
    """Open warnings across draft/computed payslips, grouped by type with
    counts + drill-down payslip ids.
    Only draft/computed payslips are 'open' (validated/paid = resolved or
    historical); internal SENT_AT sentinel warnings are excluded so they can
    never surface as an alert. Returns {warning_type, count, payslip_ids}."""
    emp_filter = Employee.id.in_(
        _filtered_employee_ids(db, department_id, employee_type) or [-1]
    )
    stmt = (
        select(PayslipWarning.warning_type, PayslipWarning.payslip_id)
        .join(Payslip, Payslip.id == PayslipWarning.payslip_id)
        .join(Employee, Employee.id == Payslip.employee_id)
        .where(
            Payslip.status.in_([PayrunStatus.draft, PayrunStatus.computed]),
            PayslipWarning.message.not_like(f"{SENT_AT_SENTINEL}%"),
            emp_filter,
        )
    )
    stmt = _period_overlap(stmt, period_start, period_end)
    rows = db.execute(stmt).all()

    grouped: dict[PayslipWarningType, list[int]] = {}
    for wtype, payslip_id in rows:
        grouped.setdefault(wtype, []).append(payslip_id)

    total_open = len({pid for _w, pid in rows})
    alerts = [
        PayrollAlertItem(warning_type=wt, count=len(ids), payslip_ids=sorted(set(ids)))
        for wt, ids in sorted(grouped.items(), key=lambda kv: kv[0].value)
    ]
    return PayrollAlertsResponse(alerts=alerts, total_open_payslips=total_open)