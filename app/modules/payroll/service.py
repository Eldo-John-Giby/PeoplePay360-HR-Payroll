"""Service layer for the Payroll module (Steve's slice).

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
# Constants
# ---------------------------------------------------------------------------

# Warning types that BLOCK Validate (documented in the module docstring).
BLOCKING_WARNING_TYPES = {
    PayslipWarningType.negative_net,
    PayslipWarningType.missing_contract,
}

# Roles that get full payroll access. EMPLOYEE gets only own-payslip views;
# HR_MANAGER has NO payroll access at all (architecture doc §4.7).
PAYROLL_ROLES = {"HR_PAYROLL_USER", "HR_PAYROLL_MANAGER", "ADMIN"}

# Sentinel prefix for the internal "payslip sent" marker (see module docstring).
SENT_AT_SENTINEL = "SENT_AT:"

# Bulk-send / PDF generation runs in a privileged internal context.
SYSTEM_USER_ID = 0


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _clamp_page(page: int, page_size: int) -> tuple[int, int]:
    """Pagination clamping: page >= 1, 1 <= page_size <= 200 (§4.4/§5.5)."""
    return max(page, 1), min(max(page_size, 1), 200)


def _paginate(db: Session, stmt, page: int, page_size: int) -> tuple[list, int, int, int]:
    page, page_size = _clamp_page(page, page_size)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    items = list(db.scalars(stmt.offset((page - 1) * page_size).limit(page_size)).all())
    return items, total, page, page_size


def _default_payrun_name(period_start: date) -> str:
    return f"Payrun — {period_start.strftime('%B %Y')}"


def _is_sentinel(warning: PayslipWarning) -> bool:
    return warning.message.startswith(SENT_AT_SENTINEL)


def _user_has_payroll_role(user: User) -> bool:
    return bool({r.name for r in user.roles} & PAYROLL_ROLES)


def _system_user() -> User:
    """Privileged internal context for bulk-send PDF generation (the batch
    endpoint is already gated to payroll roles by the router)."""
    return User(id=SYSTEM_USER_ID, email="system@peoplepay360.local",
                roles=[Role(name="ADMIN")])


# ---------------------------------------------------------------------------
# Salary Rules CRUD
# ---------------------------------------------------------------------------


def get_salary_rule_or_404(db: Session, rule_id: int) -> SalaryRule:
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
    return SalaryRuleRead.model_validate(get_salary_rule_or_404(db, rule_id))


def create_salary_rule(db: Session, payload: SalaryRuleCreate) -> SalaryRuleRead:
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
    (PATCH payloads may have changed computation_method + fields separately)."""
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
    ON DELETE RESTRICT from salary_structure_rules anyway."""
    rule = get_salary_rule_or_404(db, rule_id)
    rule.is_active = False
    db.commit()
    db.refresh(rule)
    return SalaryRuleRead.model_validate(rule)


# ---------------------------------------------------------------------------
# Salary Structures CRUD + ordered rules
# ---------------------------------------------------------------------------


def get_salary_structure_or_404(db: Session, structure_id: int) -> SalaryStructure:
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

    # Atomic replace inside one transaction.
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
# Payrun wizard (Steps 1 & 2)
# ---------------------------------------------------------------------------


def _validate_scope(db: Session, scope: PayrunScope) -> None:
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
    overlaps [period_start, period_end]."""
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
    Returns the scope echoed back + eligible employees for Step 2."""
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
    if inactive:
        raise ValidationException(
            "Employee(s) are no longer active: "
            + ", ".join(str(m) for m in inactive)
            + ". Refresh the selection and retry."
        )

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


def _commit_with_lock_guard(db: Session) -> None:
    """Commit, translating the Payrun optimistic-lock violation into a 409."""
    try:
        db.commit()
    except StaleDataError:
        db.rollback()
        raise ConflictException(
            "Payrun was modified concurrently — please refresh and retry."
        )


def _overlapping_payslips(db: Session, employee_id: int, payrun: Payrun) -> list[Payslip]:
    """Other payslips (across payruns) whose period overlaps this payrun."""
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
    if payrun.status == PayrunStatus.cancelled:
        raise ConflictException("A cancelled payrun cannot be computed.")
    if payrun.status == PayrunStatus.paid:
        raise ConflictException("A paid payrun cannot be recomputed.")

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

        try:
            computed = compute_payslip_for_employee(db, payrun, emp)
        except PayrollEngineError as exc:
            raise ConflictException(str(exc))

        if payslip is None:
            payslip = Payslip(
                payrun_id=payrun_id,
                employee_id=emp.id,
                period_start=payrun.period_start,
                period_end=payrun.period_end,
            )
            db.add(payslip)
            db.flush()  # need payslip.id for lines
        else:
            # Replace, don't append (idempotency).
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

        # Cross-domain warnings (read-only into Ameen's/Ambuj's data).
        if emp.bank_detail is None:
            payslip.warnings.append(
                PayslipWarning(
                    warning_type=PayslipWarningType.missing_bank_details,
                    message="No bank details on file — payout will be blocked "
                    "until added.",
                )
            )
            warnings_added += 1

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


def get_payslip_or_404(db: Session, payslip_id: int) -> Payslip:
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
    payslip = get_payslip_or_404(db, payslip_id)
    return _payslip_to_read(payslip)


def _payslip_to_read(payslip: Payslip) -> PayslipRead:
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
    """Current employee's own payslips (EMPLOYEE self-service)."""
    if user.employee is None:
        raise NotFoundException("No employee is linked to this account.")
    return list_payslips(
        db, page=page, page_size=page_size, employee_id=user.employee_id
    )


def can_access_payslip(user: User, payslip: Payslip) -> bool:
    """Payroll roles see everything; EMPLOYEE only their own payslip."""
    if _user_has_payroll_role(user):
        return True
    return user.employee is not None and user.employee.id == payslip.employee_id


def get_payslip_pdf(db: Session, payslip_id: int, user: User) -> tuple[bytes, str]:
    """Streams a generated PDF. EMPLOYEE role may only fetch their own
    (403 otherwise); HR_MANAGER has no payroll access at all."""
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
    """Shared filter-building helper: department + employee_type compose."""
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
    (TimeOffRequest uses date_from/date_to)."""
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
    """Map working_schedule_id -> expected working days in the period."""
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
    emp_ids = _filtered_employee_ids(db, department_id, employee_type)
    emp_filter = Employee.id.in_(emp_ids) if emp_ids else Employee.id.in_([])

    # total_net_salary_paid — PAID payslips ONLY (the demo-breaking bug).
    paid_stmt = (
        select(func.coalesce(func.sum(Payslip.net_salary), 0))
        .join(Employee, Employee.id == Payslip.employee_id)
        .where(Payslip.status == PayrunStatus.paid)
    )
    paid_stmt = _period_overlap(paid_stmt, period_start, period_end)
    paid_stmt = paid_stmt.where(emp_filter)
    total_paid = Decimal(db.scalar(paid_stmt) or 0)

    gen_stmt = (
        select(func.count(Payslip.id))
        .join(Employee, Employee.id == Payslip.employee_id)
        .where(Payslip.status != PayrunStatus.cancelled)
    )
    gen_stmt = _period_overlap(gen_stmt, period_start, period_end)
    gen_stmt = gen_stmt.where(emp_filter)
    payslips_generated = db.scalar(gen_stmt) or 0

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

    # approved time-off days (day-unit types only) overlapping the period.
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
    useful without filters."""
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
    """present_and_ontime / total_expected_days over the filtered period."""
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
    """{department_name, total_salary (paid net), headcount} — bar chart data."""
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
    """Line chart: last N months of PAID payslips (months with no data -> 0)."""
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
    months). Returns the first day of the shifted month."""
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
    Defaults to the current calendar month when no period is given."""
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
    with period overlap (day-unit types only for the days sum)."""
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
    counts + drill-down payslip ids."""
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