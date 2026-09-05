"""Payroll tests (Steve's slice) — engine unit tests + DB-backed service tests.

Engine tests are pure: no database, no FastAPI (they build `SalaryRule` objects
in memory and run the engine functions directly — definition of done §7).

Service tests need PostgreSQL at DATABASE_URL (`docker compose up -d db` +
`alembic upgrade head`); they SKIP with a clear message when the DB is
unreachable so `pytest` still passes on a bare checkout. All service tests run
inside a transaction that is rolled back afterwards — the seeded/demo data is
never modified.
"""

import uuid
from datetime import date, datetime, time, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.base import Base
from app.core.config import settings
from app.core.exceptions import ConflictException, ValidationException
from app.core.security import create_access_token
from app.main import app
from app.models.attendance import Attendance
from app.models.auth import Role, User
from app.models.employee import Contract, Employee, EmployeeBankDetail
from app.models.enums import (
    AttendanceStatus,
    ComputationMethod,
    ContractStatus,
    EmployeeStatus,
    EmployeeType,
    PayslipWarningType,
    PayrunStatus,
    SalaryRuleCategory,
    ScheduleType,
    TimeOffRequestStatus,
    TimeOffUnit,
)
from app.models.organization import Company, Department, JobPosition, WorkingSchedule, WorkingScheduleLine
from app.models.payroll import (
    Payrun,
    PayrunEmployee,
    Payslip,
    PayslipWarning,
    SalaryRule,
    SalaryStructure,
    SalaryStructureRule,
)
from app.models.timeoff import TimeOffRequest, TimeOffType
from app.modules.payroll import engine, service
from app.modules.payroll.engine import (
    PayrollEngineError,
    compute_payslip_for_employee,
    count_worked_days,
    evaluate_formula,
    expected_working_days,
    resolve_applicable_contract,
    run_engine,
)
from app.schemas.payroll import PayrunCreate, PayrunScope

# ===========================================================================
# Pure engine unit tests (no DB)
# ===========================================================================


def _rule(
    code: str,
    category: SalaryRuleCategory,
    method: ComputationMethod,
    amount: Decimal | None = None,
    percentage: Decimal | None = None,
    base: str | None = None,
    formula: str | None = None,
    seq: int = 10,
    rid: int = 0,
) -> SalaryRule:
    r = SalaryRule(
        code=code, name=code, category=category, computation_method=method,
        amount=amount, percentage=percentage, percentage_base_code=base,
        formula=formula, default_sequence=seq, is_active=True,
    )
    r.id = rid
    r.sequence = seq  # plain attribute; run_engine reads it when present
    return r


def test_formula_evaluator_basics():
    ctx = {"BASIC": Decimal("50000.00"), "HRA": Decimal("10000.00")}
    assert evaluate_formula("BASIC + HRA", ctx) == Decimal("60000.00")
    assert evaluate_formula("BASIC - HRA", ctx) == Decimal("40000.00")
    assert evaluate_formula("BASIC * 0.12", ctx) == Decimal("6000.00")
    assert evaluate_formula("max(BASIC, HRA)", ctx) == Decimal("50000.00")
    assert evaluate_formula("min(BASIC, HRA) + round(HRA / 3, 2)", ctx) == Decimal("13333.33")


def test_formula_evaluator_rejects_dangerous_code():
    ctx = {"BASIC": Decimal("1")}
    for evil in [
        "__import__('os')",
        "(1).__class__",
        "BASIC.__class__",
        "[x for x in []]",
        "lambda: 1",
        "open('/etc/passwd')",
    ]:
        with pytest.raises(PayrollEngineError):
            evaluate_formula(evil, ctx)


def test_engine_basic_structure(prompt_test_1_structure):
    """Prompt §6 test 1: BASIC (% of CONTRACT_WAGE) -> HRA (% of BASIC) ->
    NET (formula BASIC + HRA) produces correct amounts for a sample wage."""
    res = run_engine(prompt_test_1_structure, {"CONTRACT_WAGE": Decimal("50000.00")})
    lines = {l.code: l.amount for l in res.lines}
    assert lines["BASIC"] == Decimal("50000.00")
    assert lines["HRA"] == Decimal("10000.00")  # 20% of BASIC
    assert lines["NET"] == Decimal("60000.00")
    assert res.gross_salary == Decimal("60000.00")
    assert res.net_salary == Decimal("60000.00")
    assert [l.sequence for l in res.lines] == [10, 20, 30]


def test_engine_forward_reference_becomes_warning():
    """Prompt §2.3.2: percentage base with a HIGHER sequence -> controlled
    warning + amount 0, engine keeps computing the other rules."""
    rules = [
        _rule("EARLY", SalaryRuleCategory.allowance, ComputationMethod.percentage,
              percentage=Decimal("10.00"), base="LATER", seq=10),
        _rule("LATER", SalaryRuleCategory.allowance, ComputationMethod.fixed,
              amount=Decimal("100.00"), seq=20),
    ]
    res = run_engine(rules, {"CONTRACT_WAGE": Decimal("0")})
    lines = {l.code: l.amount for l in res.lines}
    assert lines["EARLY"] == Decimal("0")
    assert lines["LATER"] == Decimal("100.00")
    assert any(
        w[0] == PayslipWarningType.other and "EARLY" in w[1] for w in res.warnings
    )


def test_engine_unknown_formula_name_warning():
    rules = [
        _rule("BASIC", SalaryRuleCategory.basic, ComputationMethod.fixed,
              amount=Decimal("1000.00"), seq=10),
        _rule("BROKEN", SalaryRuleCategory.allowance, ComputationMethod.formula,
              formula="BASIC + DOES_NOT_EXIST", seq=20),
    ]
    res = run_engine(rules, {"CONTRACT_WAGE": Decimal("0")})
    assert {l.code: l.amount for l in res.lines}["BROKEN"] == Decimal("0")
    assert res.net_salary == Decimal("1000.00")  # rest of the payslip still computes


def test_engine_negative_net_warning():
    rules = [
        _rule("BASIC", SalaryRuleCategory.basic, ComputationMethod.fixed,
              amount=Decimal("1000.00"), seq=10),
        _rule("NET", SalaryRuleCategory.net, ComputationMethod.formula,
              formula="BASIC - 5000", seq=20),
    ]
    res = run_engine(rules, {"CONTRACT_WAGE": Decimal("0")})
    assert res.net_salary == Decimal("-4000.00")
    assert any(w[0] == PayslipWarningType.negative_net for w in res.warnings)


def test_engine_empty_structure_warning():
    res = run_engine([], {"CONTRACT_WAGE": Decimal("50000.00")})
    assert res.gross_salary == Decimal("0")
    assert res.net_salary == Decimal("0")
    assert res.lines == []
    assert any("no rules" in w[1] for w in res.warnings)


def test_engine_zero_working_days_no_divide_by_zero():
    """Prompt §2.5: (WORKED_DAYS / TOTAL_WORKING_DAYS) * BASIC must not crash
    when TOTAL_WORKING_DAYS == 0."""
    rules = [
        _rule("BASIC", SalaryRuleCategory.basic, ComputationMethod.fixed,
              amount=Decimal("50000.00"), seq=10),
        _rule("PRORATE", SalaryRuleCategory.allowance, ComputationMethod.formula,
              formula="(WORKED_DAYS / TOTAL_WORKING_DAYS) * BASIC", seq=20),
    ]
    res = run_engine(
        rules,
        {"CONTRACT_WAGE": Decimal("0"), "WORKED_DAYS": Decimal("0"),
         "TOTAL_WORKING_DAYS": Decimal("0")},
    )
    assert {l.code: l.amount for l in res.lines}["PRORATE"] == Decimal("0")
    assert res.gross_salary == Decimal("50000.00")  # BASIC still computes


def test_engine_gross_net_fallback_without_explicit_rules():
    """No GROSS/NET rules -> gross = basic+allowance, net = gross - deductions,
    with an explicit warning (prompt §2.3.5/2.3.6)."""
    rules = [
        _rule("BASIC", SalaryRuleCategory.basic, ComputationMethod.fixed,
              amount=Decimal("1000.00"), seq=10),
        _rule("ALLOW", SalaryRuleCategory.allowance, ComputationMethod.fixed,
              amount=Decimal("200.00"), seq=20),
        _rule("DED", SalaryRuleCategory.deduction, ComputationMethod.fixed,
              amount=Decimal("100.00"), seq=30),
    ]
    res = run_engine(rules, {"CONTRACT_WAGE": Decimal("0")})
    assert res.gross_salary == Decimal("1200.00")
    assert res.net_salary == Decimal("1100.00")
    assert any("no explicit NET rule" in w[1] for w in res.warnings)


def test_engine_rules_ordered_by_sequence():
    rules = [
        _rule("ZED", SalaryRuleCategory.allowance, ComputationMethod.fixed,
              amount=Decimal("1"), seq=30),
        _rule("ALPHA", SalaryRuleCategory.allowance, ComputationMethod.fixed,
              amount=Decimal("2"), seq=10),
    ]
    res = run_engine(rules, {})
    assert [l.code for l in res.lines] == ["ALPHA", "ZED"]


def test_engine_percentage_base_missing_from_structure_warning():
    """Prompt §2.5: percentage_base_code referencing a rule NOT in this
    structure -> warning + 0, not a 500."""
    rules = [
        _rule("BASIC", SalaryRuleCategory.basic, ComputationMethod.percentage,
              percentage=Decimal("100.00"), base="CONTRACT_WAGE", seq=10),
        _rule("HRA", SalaryRuleCategory.allowance, ComputationMethod.percentage,
              percentage=Decimal("10.00"), base="GLOBAL_ONLY_RULE", seq=20),
    ]
    res = run_engine(rules, {"CONTRACT_WAGE": Decimal("1000.00")})
    assert {l.code: l.amount for l in res.lines}["HRA"] == Decimal("0")
    assert any("GLOBAL_ONLY_RULE" in w[1] for w in res.warnings)


@pytest.fixture
def prompt_test_1_structure():
    return [
        _rule("BASIC", SalaryRuleCategory.basic, ComputationMethod.percentage,
              percentage=Decimal("100.00"), base="CONTRACT_WAGE", seq=10),
        _rule("HRA", SalaryRuleCategory.allowance, ComputationMethod.percentage,
              percentage=Decimal("20.00"), base="BASIC", seq=20),
        _rule("NET", SalaryRuleCategory.net, ComputationMethod.formula,
              formula="BASIC + HRA", seq=30),
    ]


# ===========================================================================
# DB-backed service tests (skip when PostgreSQL is unreachable)
# ===========================================================================

_TIME_OFF_VIEW_SQL = """
CREATE OR REPLACE VIEW v_time_off_balances AS
SELECT t.employee_id, t.time_off_type_id,
    COALESCE(a.allocated, 0)::NUMERIC(12,2) AS allocated,
    COALESCE(r.taken, 0)::NUMERIC(12,2) AS taken,
    (COALESCE(a.allocated, 0) - COALESCE(r.taken, 0))::NUMERIC(12,2) AS remaining
FROM (
    SELECT employee_id, time_off_type_id FROM time_off_allocations
    UNION
    SELECT employee_id, time_off_type_id FROM time_off_requests
) t
LEFT JOIN (
    SELECT employee_id, time_off_type_id, SUM(allocated_amount) AS allocated
    FROM time_off_allocations WHERE status = 'approved'
      AND (valid_to IS NULL OR valid_to >= CURRENT_DATE)
    GROUP BY employee_id, time_off_type_id
) a ON a.employee_id = t.employee_id AND a.time_off_type_id = t.time_off_type_id
LEFT JOIN (
    SELECT employee_id, time_off_type_id, SUM(duration) AS taken
    FROM time_off_requests WHERE status = 'approved'
    GROUP BY employee_id, time_off_type_id
) r ON r.employee_id = t.employee_id AND r.time_off_type_id = t.time_off_type_id
"""

_SCHEDULE_VIEW_SQL = """
CREATE OR REPLACE VIEW v_working_schedule_hours AS
SELECT working_schedule_id,
    ROUND(EXTRACT(EPOCH FROM SUM(end_time - start_time
        - make_interval(mins => break_minutes))) / 3600.0, 2)::NUMERIC(10,2)
        AS total_weekly_hours
FROM working_schedule_lines GROUP BY working_schedule_id
"""


@pytest.fixture(scope="session")
def db_engine():
    from sqlalchemy.exc import OperationalError

    from app.core.database import engine as app_engine

    try:
        with app_engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except OperationalError as exc:
        pytest.skip(
            f"PostgreSQL unreachable at {settings.DATABASE_URL}: {exc} — "
            "start it with `docker compose up -d db` + `alembic upgrade head`."
        )
    with app_engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
    Base.metadata.create_all(app_engine)  # idempotent; migration already applied
    with app_engine.begin() as conn:
        conn.execute(text(_TIME_OFF_VIEW_SQL))
        conn.execute(text(_SCHEDULE_VIEW_SQL))
    return app_engine


@pytest.fixture()
def db(db_engine):
    """Per-test rolled-back session: service commits become savepoint releases,
    and the outer transaction is rolled back at teardown — the DB is never
    modified by tests."""
    conn = db_engine.connect()
    trans = conn.begin()
    session = Session(
        bind=conn,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    )
    yield session
    session.close()
    trans.rollback()
    conn.close()


# -- fixture builders --------------------------------------------------------

_CONTRACT_DERIVED_RULES = [
    ("BASIC", SalaryRuleCategory.basic, ComputationMethod.percentage,
     None, Decimal("100.00"), "CONTRACT_WAGE", None, 10),
    ("HRA", SalaryRuleCategory.allowance, ComputationMethod.percentage,
     None, Decimal("20.00"), "BASIC", None, 20),
    ("NET", SalaryRuleCategory.net, ComputationMethod.formula,
     None, None, None, "BASIC + HRA", 30),
]

# Fix 2: a structure whose formula lines reference the leave context keys
# (PAID_LEAVE_DAYS / UNPAID_LEAVE_DAYS pass through _make_structure's code
# rewrite untouched — they are virtual keys, not rule codes).
_LEAVE_DERIVED_RULES = [
    ("BASIC", SalaryRuleCategory.basic, ComputationMethod.fixed,
     Decimal("1000.00"), None, None, None, 10),
    ("PAID_BONUS", SalaryRuleCategory.allowance, ComputationMethod.formula,
     None, None, None, "PAID_LEAVE_DAYS * 100", 20),
    ("UNPAID_DED", SalaryRuleCategory.deduction, ComputationMethod.formula,
     None, None, None, "UNPAID_LEAVE_DAYS * 50", 30),
]


def _make_structure(db: Session, rules_spec=None):
    """Create a salary structure + rules with UNIQUE rule codes per test
    (salary_rules.code is globally unique — the seeded DB already owns
    BASIC/HRA/NET). Formula/base references are rewritten to the suffixed
    codes; CONTRACT_WAGE and other virtual keys pass through untouched."""
    suffix = uuid.uuid4().hex[:4].upper()
    spec = rules_spec or _CONTRACT_DERIVED_RULES
    code_map = {t[0]: f"{t[0]}_{suffix}" for t in spec}

    def _rewrite(value: str | None) -> str | None:
        if value is None:
            return None
        for old, new in code_map.items():
            value = value.replace(old, new)
        return value

    structure = SalaryStructure(
        name=f"Structure {suffix}", code=f"STR_{suffix}", is_active=True
    )
    db.add(structure)
    db.flush()
    rules = {}
    for (code, category, method, amount, percentage, base, formula, seq) in spec:
        new_code = code_map[code]
        rule = SalaryRule(
            code=new_code, name=new_code, category=category,
            computation_method=method, amount=amount, percentage=percentage,
            percentage_base_code=_rewrite(base), formula=_rewrite(formula),
            default_sequence=seq, is_active=True,
        )
        db.add(rule)
        db.flush()
        db.add(SalaryStructureRule(
            salary_structure_id=structure.id, salary_rule_id=rule.id, sequence=seq
        ))
        rules[code] = rule
    return structure, rules


def _make_employee(
    db: Session,
    structure: SalaryStructure,
    *,
    employee_type: EmployeeType = EmployeeType.full_time,
    with_contract: bool = True,
    with_bank: bool = True,
    wage: Decimal = Decimal("50000.00"),
    email_suffix: str | None = None,
):
    suffix = email_suffix or uuid.uuid4().hex[:6]
    company = Company(name=f"Co {suffix}", is_active=True)
    db.add(company)
    dept = Department(name=f"Dept {suffix}", company_id=company.id)
    db.add(dept)
    db.flush()  # department id needed for the job position FK
    pos = JobPosition(title=f"Engineer {suffix}", department_id=dept.id)
    db.add(pos)
    sched = WorkingSchedule(
        name=f"Sched {suffix}", schedule_type=ScheduleType.full_time,
        company_id=company.id, is_active=True,
    )
    db.add(sched)
    db.flush()
    for dow in range(5):
        db.add(WorkingScheduleLine(
            working_schedule_id=sched.id, day_of_week=dow,
            start_time=time(9, 0), end_time=time(18, 0), break_minutes=60,
        ))
    emp = Employee(
        full_name=f"Employee {suffix}", work_email=f"emp.{suffix}@test.local",
        department_id=dept.id, job_position_id=pos.id,
        working_schedule_id=sched.id, employee_type=employee_type,
        status=EmployeeStatus.active, date_of_joining=date(2026, 1, 1),
        company_id=company.id,
    )
    db.add(emp)
    db.flush()
    contract = None
    if with_contract:
        contract = Contract(
            contract_number=f"CON-{suffix}",
            employee_id=emp.id, department_id=dept.id, job_position_id=pos.id,
            working_schedule_id=sched.id, salary_structure_id=structure.id,
            wage_monthly=wage, start_date=date(2026, 1, 1), end_date=None,
            status=ContractStatus.running,
        )
        db.add(contract)
        db.flush()
    if with_bank:
        db.add(EmployeeBankDetail(
            employee_id=emp.id, account_holder_name=emp.full_name,
            bank_name="Test Bank", account_number="1234567890", ifsc_or_swift="TEST0001",
        ))
    db.flush()
    return emp, contract


def _make_payrun(db: Session, structure, period_start, period_end, employees):
    payrun = Payrun(
        name=f"Payrun {uuid.uuid4().hex[:6]}",
        salary_structure_id=structure.id,
        period_start=period_start, period_end=period_end,
        status=PayrunStatus.draft, created_by_user_id=_make_user(db).id,
    )
    db.add(payrun)
    db.flush()
    for emp in employees:
        db.add(PayrunEmployee(payrun_id=payrun.id, employee_id=emp.id))
    db.flush()
    return payrun


def _make_user(db: Session) -> User:
    suffix = uuid.uuid4().hex[:6]
    user = User(
        email=f"user.{suffix}@test.local", hashed_password="x",
        is_active=True,
    )
    db.add(user)
    db.flush()
    return user


# -- contract resolution -----------------------------------------------------


def test_resolve_applicable_contract(db):
    structure, _ = _make_structure(db)
    emp, running = _make_employee(db, structure)
    # expired contract for a past period
    expired = Contract(
        contract_number=f"CON-X-{uuid.uuid4().hex[:6]}",
        employee_id=emp.id, department_id=emp.department_id,
        job_position_id=emp.job_position_id,
        working_schedule_id=emp.working_schedule_id,
        salary_structure_id=structure.id, wage_monthly=Decimal("30000.00"),
        start_date=date(2025, 1, 1), end_date=date(2025, 12, 31),
        status=ContractStatus.expired,
    )
    db.add(expired)
    db.flush()

    # running contract covers a current period
    chosen, warnings = resolve_applicable_contract(db, emp.id, date(2026, 9, 1), date(2026, 9, 30))
    assert chosen is not None and chosen.id == running.id
    assert warnings == []

    # expired contract is correct for a *past* period
    chosen, warnings = resolve_applicable_contract(db, emp.id, date(2025, 6, 1), date(2025, 6, 30))
    assert chosen is not None and chosen.id == expired.id

    # no contract at all -> (None, [])
    no_contract_emp, _ = _make_employee(db, structure, with_contract=False)
    chosen, warnings = resolve_applicable_contract(db, no_contract_emp.id, date(2026, 9, 1), date(2026, 9, 30))
    assert chosen is None and warnings == []


# -- compute lifecycle -------------------------------------------------------


def test_compute_no_contract_zero_salary_and_blocked_validate(db):
    """Prompt §6 test 3: no contract -> missing_contract warning, zero salary,
    Validate blocked until resolved."""
    structure, _ = _make_structure(db)
    emp, _ = _make_employee(db, structure, with_contract=False)
    payrun = _make_payrun(db, structure, date(2026, 9, 1), date(2026, 9, 30), [emp])

    result = service.compute_payrun(db, payrun.id)
    assert result.payslips_computed == 1

    ps = service.list_payslips(db, employee_id=emp.id).items[0]
    slip = service.get_payslip(db, ps.id)
    assert slip.net_salary == 0 and slip.gross_salary == 0
    assert any(
        w.warning_type == PayslipWarningType.missing_contract for w in slip.warnings
    )

    with pytest.raises(ConflictException):
        service.validate_payrun(db, payrun.id)


def test_full_lifecycle_and_mark_paid_twice_conflict(db):
    """Prompt §6 test 5: mark-paid twice -> second call 409."""
    structure, _ = _make_structure(db)
    emp, _ = _make_employee(db, structure, wage=Decimal("50000.00"))
    payrun = _make_payrun(db, structure, date(2026, 9, 1), date(2026, 9, 30), [emp])

    service.compute_payrun(db, payrun.id)
    assert service.get_payrun(db, payrun.id).status == PayrunStatus.computed

    # validate blocked on draft? no — computed, and no blocking warnings
    validated = service.validate_payrun(db, payrun.id)
    assert validated.validated_payslips == 1
    assert service.get_payrun(db, payrun.id).status == PayrunStatus.validated

    paid = service.mark_paid(db, payrun.id)
    assert paid.paid_payslips == 1
    assert service.get_payrun(db, payrun.id).status == PayrunStatus.paid

    with pytest.raises(ConflictException):
        service.mark_paid(db, payrun.id)


def test_recompute_skips_validated_payslip(db):
    """Prompt §6 test 4: recompute an already-validated payslip -> skipped,
    reported clearly, never overwritten."""
    structure, _ = _make_structure(db)
    emp, _ = _make_employee(db, structure, wage=Decimal("60000.00"))
    payrun = _make_payrun(db, structure, date(2026, 9, 1), date(2026, 9, 30), [emp])

    service.compute_payrun(db, payrun.id)
    service.validate_payrun(db, payrun.id)

    result = service.compute_payrun(db, payrun.id)
    assert result.payslips_computed == 0
    assert len(result.payslips_skipped) == 1
    assert "validated" in result.payslips_skipped[0].reason
    # payslip untouched
    ps = service.list_payslips(db, employee_id=emp.id).items[0]
    assert ps.status == PayrunStatus.validated


def test_recompute_replaces_lines_not_appends(db):
    structure, _ = _make_structure(db)
    emp, _ = _make_employee(db, structure)
    payrun = _make_payrun(db, structure, date(2026, 9, 1), date(2026, 9, 30), [emp])

    service.compute_payrun(db, payrun.id)
    first = service.list_payslips(db, employee_id=emp.id).items[0]
    slip1 = service.get_payslip(db, first.id)
    assert len(slip1.lines) == 3

    service.compute_payrun(db, payrun.id)  # idempotent re-run
    second = service.list_payslips(db, employee_id=emp.id).items[0]
    slip2 = service.get_payslip(db, second.id)
    assert len(slip2.lines) == 3  # replaced, not appended


def test_compute_worked_days_from_attendance(db):
    structure, _ = _make_structure(db)
    emp, _ = _make_employee(db, structure)
    payrun = _make_payrun(db, structure, date(2026, 9, 1), date(2026, 9, 30), [emp])
    for day in (1, 2, 3):
        db.add(Attendance(
            employee_id=emp.id,
            check_in=datetime(2026, 9, day, 9, 0, tzinfo=timezone.utc),
            check_out=datetime(2026, 9, day, 18, 0, tzinfo=timezone.utc),
            worked_hours=Decimal("8.00"), status=AttendanceStatus.present,
        ))
    db.flush()
    assert count_worked_days(db, emp.id, date(2026, 9, 1), date(2026, 9, 30)) == 3
    assert expected_working_days(db, emp, date(2026, 9, 1), date(2026, 9, 30)) == 22

    service.compute_payrun(db, payrun.id)
    slip = service.get_payslip(db, service.list_payslips(db, employee_id=emp.id).items[0].id)
    assert slip.worked_days == 3


def test_approved_leave_days_enter_engine_context(db):
    """Fix 2: approved day-unit leave overlapping the payrun period is split
    by affects_payroll into PAID_LEAVE_DAYS / UNPAID_LEAVE_DAYS, and structure
    formulas can reference them. Requests that are unapproved, outside the
    period, or hours-unit are ignored."""
    structure, rules = _make_structure(db, _LEAVE_DERIVED_RULES)
    emp, _ = _make_employee(db, structure, wage=Decimal("1000.00"))
    payrun = _make_payrun(db, structure, date(2026, 9, 1), date(2026, 9, 30), [emp])

    def _type(name: str, unit: TimeOffUnit, affects_payroll: bool) -> TimeOffType:
        t = TimeOffType(
            name=f"{name} {uuid.uuid4().hex[:6]}", unit=unit,
            requires_allocation=False, requires_approval=True,
            affects_payroll=affects_payroll, is_active=True,
        )
        db.add(t)
        return t

    paid = _type("Paid", TimeOffUnit.days, True)
    unpaid = _type("Unpaid", TimeOffUnit.days, False)
    wfh = _type("WFH", TimeOffUnit.hours, True)
    db.flush()

    def _request(t: TimeOffType, d_from: date, d_to: date,
                 status: TimeOffRequestStatus) -> None:
        duration = (
            Decimal("8.00") if t.unit == TimeOffUnit.hours
            else Decimal(str((d_to - d_from).days + 1))
        )
        db.add(TimeOffRequest(
            employee_id=emp.id, time_off_type_id=t.id,
            date_from=d_from, date_to=d_to, duration=duration, status=status,
        ))

    # 3 paid days fully inside the period (Sep 10-12).
    _request(paid, date(2026, 9, 10), date(2026, 9, 12), TimeOffRequestStatus.approved)
    # Straddles the period end -> only Sep 28-30 (3 days) count, not Oct 1-2.
    _request(paid, date(2026, 9, 28), date(2026, 10, 2), TimeOffRequestStatus.approved)
    # 2 unpaid days inside the period.
    _request(unpaid, date(2026, 9, 20), date(2026, 9, 21), TimeOffRequestStatus.approved)
    # Ignored: not approved.
    _request(paid, date(2026, 9, 5), date(2026, 9, 5), TimeOffRequestStatus.to_approve)
    # Ignored: outside the period.
    _request(paid, date(2026, 10, 5), date(2026, 10, 6), TimeOffRequestStatus.approved)
    # Ignored: hours-unit types have no day granularity.
    _request(wfh, date(2026, 9, 15), date(2026, 9, 15), TimeOffRequestStatus.approved)
    db.flush()

    result = compute_payslip_for_employee(db, payrun, emp)
    lines = {l.code: l.amount for l in result.lines}
    assert lines[rules["PAID_BONUS"].code] == Decimal("600.00")  # (3 + 3) * 100
    assert lines[rules["UNPAID_DED"].code] == Decimal("100.00")  # 2 * 50
    assert result.gross_salary == Decimal("1600.00")
    assert result.net_salary == Decimal("1500.00")
    # The only warning is the documented "no explicit NET rule" fallback.
    assert not any(
        "PAID_LEAVE_DAYS" in w[1] or "UNPAID_LEAVE_DAYS" in w[1]
        for w in result.warnings
    )


def test_engine_leave_days_default_to_zero_without_requests(db):
    """Fix 2: with no approved leave overlapping the period, the leave
    context keys are 0 and a structure that references them computes exactly
    as before (no unknown-name warning, zero leave lines)."""
    structure, rules = _make_structure(db, _LEAVE_DERIVED_RULES)
    emp, _ = _make_employee(db, structure, wage=Decimal("1000.00"))
    payrun = _make_payrun(db, structure, date(2026, 9, 1), date(2026, 9, 30), [emp])

    result = compute_payslip_for_employee(db, payrun, emp)
    lines = {l.code: l.amount for l in result.lines}
    assert lines[rules["PAID_BONUS"].code] == Decimal("0.00")
    assert lines[rules["UNPAID_DED"].code] == Decimal("0.00")
    assert result.gross_salary == Decimal("1000.00")
    assert result.net_salary == Decimal("1000.00")
    assert not any(
        "PAID_LEAVE_DAYS" in w[1] or "UNPAID_LEAVE_DAYS" in w[1]
        for w in result.warnings
    )


def test_cancel_only_from_draft_or_computed(db):
    structure, _ = _make_structure(db)
    emp, _ = _make_employee(db, structure)
    payrun = _make_payrun(db, structure, date(2026, 9, 1), date(2026, 9, 30), [emp])

    cancelled = service.cancel_payrun(db, payrun.id)  # draft -> ok
    assert cancelled.status == PayrunStatus.cancelled

    with pytest.raises(ConflictException):
        service.cancel_payrun(db, payrun.id)  # already cancelled


# -- wizard ------------------------------------------------------------------


def test_draft_scope_filters_and_flags(db):
    structure, _ = _make_structure(db)
    ft, _ = _make_employee(db, structure, employee_type=EmployeeType.full_time)
    pt, _ = _make_employee(db, structure, employee_type=EmployeeType.part_time, with_contract=False)

    scope = PayrunScope(
        salary_structure_id=structure.id,
        period_start=date(2026, 9, 1), period_end=date(2026, 9, 30),
        employee_type_filter=EmployeeType.full_time,
    )
    resp = service.draft_scope(db, scope)
    ids = {e.id for e in resp.eligible_employees}
    assert ft.id in ids and pt.id not in ids
    by_id = {e.id: e for e in resp.eligible_employees}
    assert by_id[ft.id].has_contract is True

    # no-contract employee is still eligible but flagged
    full_scope = PayrunScope(
        salary_structure_id=structure.id,
        period_start=date(2026, 9, 1), period_end=date(2026, 9, 30),
    )
    full = service.draft_scope(db, full_scope)
    all_by_id = {e.id: e for e in full.eligible_employees}
    assert pt.id in all_by_id
    assert all_by_id[pt.id].has_contract is False


def test_create_payrun_validates_scope_and_employees(db):
    structure, _ = _make_structure(db)
    emp, _ = _make_employee(db, structure)
    scope = PayrunScope(
        salary_structure_id=structure.id,
        period_start=date(2026, 9, 1), period_end=date(2026, 9, 30),
    )
    actor = _make_user(db)

    created = service.create_payrun(
        db, PayrunCreate(scope=scope, employee_ids=[emp.id]), actor
    )
    assert created.status == PayrunStatus.draft

    # unknown employee id -> 422
    with pytest.raises(ValidationException):
        service.create_payrun(
            db, PayrunCreate(scope=scope, employee_ids=[999_999_999]), actor
        )

    # employee not matching the department scope -> 422
    other, _ = _make_employee(db, structure, email_suffix="otherdept")
    bad_scope = PayrunScope(
        salary_structure_id=structure.id,
        period_start=date(2026, 9, 1), period_end=date(2026, 9, 30),
        department_filter_id=emp.department_id,
    )
    with pytest.raises(ValidationException):
        service.create_payrun(
            db, PayrunCreate(scope=bad_scope, employee_ids=[other.id]), actor
        )


# -- structures --------------------------------------------------------------


def test_replace_structure_rules_duplicate_conflict(db):
    structure, rules = _make_structure(db)
    basic, hra = rules["BASIC"], rules["HRA"]
    with pytest.raises(ConflictException):
        service.replace_structure_rules(db, structure.id, [
            {"salary_rule_id": basic.id, "sequence": 10},
            {"salary_rule_id": basic.id, "sequence": 20},  # duplicate
        ])
    # valid replace works and is ordered
    updated = service.replace_structure_rules(db, structure.id, [
        {"salary_rule_id": hra.id, "sequence": 5},
        {"salary_rule_id": basic.id, "sequence": 6},
    ])
    assert [(r.sequence, r.rule.code) for r in updated.rules] == [
        (5, rules["HRA"].code), (6, rules["BASIC"].code)
    ]


def test_salary_rule_create_rejects_inconsistent_method():
    """API-layer validation: exactly one of amount/percentage/formula matching
    the method -> pydantic error (422 upstream), never reaches Postgres."""
    from pydantic import ValidationError

    from app.schemas.payroll import SalaryRuleCreate

    with pytest.raises(ValidationError):
        SalaryRuleCreate(
            code="BAD_RULE", name="Bad", category=SalaryRuleCategory.allowance,
            computation_method=ComputationMethod.fixed,  # fixed but no amount
            percentage=Decimal("10.00"),
        )
    with pytest.raises(ValidationError):
        SalaryRuleCreate(
            code="BAD2", name="Bad2", category=SalaryRuleCategory.allowance,
            computation_method=ComputationMethod.percentage,
            percentage=Decimal("10.00"),  # missing percentage_base_code
        )


# -- dashboard ---------------------------------------------------------------


def test_kpis_total_paid_counts_paid_only(db):
    """Prompt §6 test 7: total_net_salary_paid only counts PAID payslips."""
    structure, _ = _make_structure(db)
    emp, _ = _make_employee(db, structure, wage=Decimal("1000.00"))
    period_start, period_end = date(2026, 1, 1), date(2026, 1, 31)

    # one payslip PER PAYRUN (uq_payslips_payrun_employee makes two payslips
    # for the same employee in one payrun structurally impossible)
    for status, net in [
        (PayrunStatus.paid, Decimal("1000.00")),
        (PayrunStatus.computed, Decimal("500.00")),
        (PayrunStatus.validated, Decimal("700.00")),
        (PayrunStatus.draft, Decimal("200.00")),
    ]:
        payrun = _make_payrun(db, structure, period_start, period_end, [emp])
        db.add(Payslip(
            payrun_id=payrun.id, employee_id=emp.id,
            period_start=period_start, period_end=period_end,
            gross_salary=net, net_salary=net, status=status,
        ))
    db.flush()

    kpis = service.get_kpis(db, period_start, period_end, None, None)
    assert kpis.total_net_salary_paid == Decimal("1000.00")  # paid only
    assert kpis.payslips_generated == 4
    # average covers computed/validated/paid only — draft (200) is not a
    # generated amount: (1000+500+700)/3
    assert float(kpis.average_salary) == pytest.approx(733.3333, abs=0.001)


def test_monthly_trend_calendar_accurate_months(db):
    structure, _ = _make_structure(db)
    emp, _ = _make_employee(db, structure, wage=Decimal("1000.00"))
    payrun = _make_payrun(db, structure, date(2026, 8, 1), date(2026, 8, 31), [emp])
    db.add(Payslip(
        payrun_id=payrun.id, employee_id=emp.id,
        period_start=date(2026, 8, 1), period_end=date(2026, 8, 31),
        gross_salary=Decimal("1000.00"), net_salary=Decimal("1000.00"),
        status=PayrunStatus.paid,
    ))
    db.flush()
    # department filter isolates from the seeded (paid) August 2026 payrun
    trend = service.get_monthly_net_salary_trend(
        db, 6, date(2026, 3, 1), date(2026, 9, 30), emp.department_id, None
    )
    months = [t.month for t in trend]
    assert months == ["2026-04", "2026-05", "2026-06", "2026-07", "2026-08", "2026-09"]
    totals = {t.month: t.total_net_salary for t in trend}
    assert totals["2026-08"] == Decimal("1000.00")
    assert totals["2026-06"] == Decimal("0")  # missing month -> 0, not skipped


def test_attendance_overview_computes_absent_from_schedule(db):
    structure, _ = _make_structure(db)
    emp, _ = _make_employee(db, structure)
    # 2026-09-01..04 are Tue..Fri (4 working days; Sep 5 is a Saturday)
    period_start, period_end = date(2026, 9, 1), date(2026, 9, 4)
    for day in (1, 2):
        db.add(Attendance(
            employee_id=emp.id,
            check_in=datetime(2026, 9, day, 9, 0, tzinfo=timezone.utc),
            check_out=datetime(2026, 9, day, 18, 0, tzinfo=timezone.utc),
            worked_hours=Decimal("8.00"), status=AttendanceStatus.present,
        ))
    db.flush()
    # department filter isolates THIS test's employee from the seeded data
    ov = service.get_attendance_overview(
        db, period_start, period_end, emp.department_id, None
    )
    assert ov.present == 2
    assert ov.absent == 2  # expected 4 weekday days, attended 2
    assert ov.coverage_pct == 50.0
    assert ov.manual_edits == 0


# -- bulk email --------------------------------------------------------------


def test_bulk_send_skips_missing_bank_details(db):
    """Prompt §6 test 8: one employee missing bank details is skipped and
    reported; the other still sends (console transport, no SMTP needed)."""
    structure, _ = _make_structure(db)
    with_bank, _ = _make_employee(db, structure, wage=Decimal("50000.00"))
    without_bank, _ = _make_employee(db, structure, wage=Decimal("40000.00"), with_bank=False)
    payrun = _make_payrun(
        db, structure, date(2026, 9, 1), date(2026, 9, 30),
        [with_bank, without_bank],
    )

    service.compute_payrun(db, payrun.id)
    service.validate_payrun(db, payrun.id)

    result = service.send_payslips(db, payrun.id)
    by_emp = {r.employee_id: r for r in result.results}
    assert by_emp[with_bank.id].sent is True
    assert by_emp[without_bank.id].sent is False
    assert "bank" in (by_emp[without_bank.id].error or "")

    # idempotent: second click does not double-send
    again = service.send_payslips(db, payrun.id)
    again_by_emp = {r.employee_id: r for r in again.results}
    assert again_by_emp[with_bank.id].sent is False
    assert "already sent" in (again_by_emp[with_bank.id].error or "")


def test_send_requires_validated_payrun(db):
    structure, _ = _make_structure(db)
    emp, _ = _make_employee(db, structure)
    payrun = _make_payrun(db, structure, date(2026, 9, 1), date(2026, 9, 30), [emp])
    service.compute_payrun(db, payrun.id)
    with pytest.raises(ConflictException):
        service.send_payslips(db, payrun.id)  # computed, not validated


# -- RBAC via the HTTP API ---------------------------------------------------


def _ensure_role(db: Session, name: str) -> Role:
    role = db.scalar(select(Role).where(Role.name == name))
    if role is None:
        role = Role(name=name, description=f"test role {name}")
        db.add(role)
        db.commit()
    return role


def _make_api_user(role_names: list[str]) -> User:
    from app.core.database import SessionLocal

    suffix = uuid.uuid4().hex[:8]
    with SessionLocal() as db:
        user = User(
            email=f"api.{suffix}@test.local", hashed_password="x", is_active=True,
        )
        user.roles = [_ensure_role(db, r) for r in role_names]
        db.add(user)
        db.commit()
        db.refresh(user)
        return user


def test_rbac_salary_rules_read_write_split():
    """Prompt §6 test 6: HR_PAYROLL_USER GET 200, POST 403; HR_MANAGER 403;
    EMPLOYEE 403 on dashboard; ADMIN can write."""
    created_users: list[User] = []
    created_rules: list[int] = []
    try:
        payroll_user = _make_api_user(["HR_PAYROLL_USER"])
        hr_manager = _make_api_user(["HR_MANAGER"])
        employee = _make_api_user(["EMPLOYEE"])
        admin = _make_api_user(["ADMIN"])
        created_users = [payroll_user, hr_manager, employee, admin]

        client = TestClient(app)

        def auth(user: User) -> dict:
            return {"Authorization": f"Bearer {create_access_token(user.id)}"}

        # HR_PAYROLL_USER: read OK, write forbidden
        r = client.get("/api/v1/payroll/salary-rules", headers=auth(payroll_user))
        assert r.status_code == 200, r.text
        r = client.post(
            "/api/v1/payroll/salary-rules",
            headers=auth(payroll_user),
            json={
                "code": f"RBAC_{uuid.uuid4().hex[:4].upper()}",
                "name": "RBAC test rule",
                "category": "allowance",
                "computation_method": "fixed",
                "amount": "100.00",
            },
        )
        assert r.status_code == 403, r.text

        # HR_MANAGER: no payroll access at all
        r = client.get("/api/v1/payroll/salary-rules", headers=auth(hr_manager))
        assert r.status_code == 403, r.text
        r = client.get("/api/v1/dashboard/kpis", headers=auth(hr_manager))
        assert r.status_code == 403, r.text

        # EMPLOYEE: no dashboard access
        r = client.get("/api/v1/dashboard/kpis", headers=auth(employee))
        assert r.status_code == 403, r.text

        # ADMIN: full write access (positive control) — cleaned up below
        payload = {
            "code": f"ADMIN_{uuid.uuid4().hex[:4].upper()}",
            "name": "Admin created rule",
            "category": "allowance",
            "computation_method": "fixed",
            "amount": "100.00",
        }
        r = client.post(
            "/api/v1/payroll/salary-rules", headers=auth(admin), json=payload
        )
        assert r.status_code == 201, r.text
        created_rules.append(r.json()["id"])
    finally:
        from app.core.database import SessionLocal

        with SessionLocal() as db:
            if created_rules:
                db.execute(
                    SalaryRule.__table__.delete().where(
                        SalaryRule.id.in_(created_rules)
                    )
                )
            if created_users:
                db.execute(
                    User.__table__.delete().where(User.id.in_([u.id for u in created_users]))
                )
            db.commit()