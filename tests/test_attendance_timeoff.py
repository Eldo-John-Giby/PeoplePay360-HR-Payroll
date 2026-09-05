"""Attendance + Time Off tests (Ambuj's slice).

Structure mirrors `tests/test_payroll.py`:
- Pure unit tests of the status derivation / worked-hours math run with NO
  database (definition of done — the derivation logic is a pure function).
- DB-backed service tests need PostgreSQL at DATABASE_URL (`docker compose up
  -d db` + `alembic upgrade head`); they SKIP with a clear message when the
  DB is unreachable so `pytest` still passes on a bare checkout. All service
  tests run inside a transaction that is rolled back afterwards.
- A small HTTP-level RBAC section (like Steve's) proves the require_roles
  gates: EMPLOYEE gets 403 on every HR-only surface, HR_MANAGER gets 200.

Coverage map (brief §4):
- check-in -> check-out -> worked_hours, incl. an overnight case
- double check-in without checkout -> 409
- manual correction attempted by EMPLOYEE -> 403
- allocation approved -> balance reflects it; request > remaining -> 409
- two overlapping approved requests -> second approval 409
- refuse an already-approved request -> 409 (invalid transition)
- cancel approved before date_from allowed / after date_from blocked
- EMPLOYEE listing another employee's attendance/requests -> 403
"""

import uuid
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.base import Base
from app.core.config import settings
from app.core.exceptions import ConflictException, ForbiddenException
from app.core.security import create_access_token
from app.main import app
from app.models.attendance import Attendance
from app.models.auth import Role, User
from app.models.employee import Employee
from app.models.enums import (
    AllocationStatus,
    AttendanceStatus,
    EmployeeStatus,
    EmployeeType,
    ScheduleType,
    TimeOffRequestStatus,
    TimeOffUnit,
)
from app.models.organization import (
    Company,
    Department,
    JobPosition,
    WorkingSchedule,
    WorkingScheduleLine,
)
from app.models.timeoff import TimeOffRequest, TimeOffType
from app.modules.attendance_timeoff import service
from app.modules.attendance_timeoff.service import ScheduleSpec
from app.schemas.attendance_timeoff import (
    AttendanceCheckInCreate,
    AttendanceManualCreate,
    TimeOffAllocationCreate,
    TimeOffRequestCreate,
)

# ===========================================================================
# Pure derivation unit tests (no DB)
# ===========================================================================

UTC = timezone.utc

# Mon-Fri 09:00-18:00 with a 60-min break -> 8.00 expected net hours.
FT_SPEC = ScheduleSpec(
    start_time=time(9, 0),
    end_time=time(18, 0),
    break_minutes=60,
    expected_hours=Decimal("8.00"),
)


def _utc(y, mo, d, h, mi=0, s=0):
    return datetime(y, mo, d, h, mi, s, tzinfo=UTC)


def test_derive_present_late_overtime():
    """Same-day FT shift: on-time full day present; late check-in wins over
    overtime; long day without lateness is overtime (seed parity)."""
    worked, status = service.derive_attendance(
        _utc(2026, 9, 7, 9, 0), _utc(2026, 9, 7, 18, 0), FT_SPEC
    )
    assert status == AttendanceStatus.present
    assert worked == Decimal("8.00")  # 9h elapsed - 1h break

    # 20 minutes late -> late, even though the shift was a full day.
    worked, status = service.derive_attendance(
        _utc(2026, 9, 7, 9, 20), _utc(2026, 9, 7, 18, 20), FT_SPEC
    )
    assert status == AttendanceStatus.late
    assert worked == Decimal("8.00")

    # On time + 1h extra (9h net) -> overtime.
    worked, status = service.derive_attendance(
        _utc(2026, 9, 7, 9, 0), _utc(2026, 9, 7, 19, 0), FT_SPEC
    )
    assert status == AttendanceStatus.overtime
    assert worked == Decimal("9.00")

    # A late arrival that still logs overtime hours stays `late` (priority).
    worked, status = service.derive_attendance(
        _utc(2026, 9, 7, 9, 30), _utc(2026, 9, 7, 19, 30), FT_SPEC
    )
    assert status == AttendanceStatus.late


def test_derive_open_check_in_provisional_status():
    """An in-progress check-in never gets missing_checkout from the pure
    function; it is provisional present/late based on the check-in moment."""
    _worked, status = service.derive_attendance(
        _utc(2026, 9, 7, 8, 50), None, FT_SPEC
    )
    assert status == AttendanceStatus.present
    _worked, status = service.derive_attendance(
        _utc(2026, 9, 7, 9, 42), None, FT_SPEC
    )
    assert status == AttendanceStatus.late


def test_late_grace_boundary_is_strict():
    """Exactly start+grace is NOT late; a minute later is (configurable)."""
    _, status = service.derive_attendance(
        _utc(2026, 9, 7, 9, 15), _utc(2026, 9, 7, 18, 0), FT_SPEC,
        late_grace_minutes=15,
    )
    assert status == AttendanceStatus.present
    _, status = service.derive_attendance(
        _utc(2026, 9, 7, 9, 16), _utc(2026, 9, 7, 18, 0), FT_SPEC,
        late_grace_minutes=15,
    )
    assert status == AttendanceStatus.late


def test_worked_hours_overnight_delta():
    """23:50 -> 00:20 next day = 0.50h. Full timestamptz delta — never
    truncated to same-day, and no break subtracted from a sub-break session."""
    worked = service.compute_worked_hours(
        _utc(2026, 9, 7, 23, 50), _utc(2026, 9, 8, 0, 20), FT_SPEC
    )
    assert worked == Decimal("0.50")


def test_worked_hours_break_only_when_spanned():
    """A 3h morning session plausibly spans the 1h lunch; a 30-min stretch
    cannot contain it."""
    assert service.compute_worked_hours(
        _utc(2026, 9, 7, 9, 0), _utc(2026, 9, 7, 12, 0), FT_SPEC
    ) == Decimal("2.00")
    assert service.compute_worked_hours(
        _utc(2026, 9, 7, 12, 0), _utc(2026, 9, 7, 12, 30), FT_SPEC
    ) == Decimal("0.50")


def test_no_schedule_line_day_uses_average_expected_hours():
    """Sunday with a Mon-Fri schedule: no start/end to compare -> cannot be
    late, but 10h of unscheduled work beats the 8h daily average -> overtime
    (documented simplification for the no-line-for-Sunday edge case)."""
    sunday_spec = ScheduleSpec(None, None, 0, Decimal("8.00"))
    worked, status = service.derive_attendance(
        _utc(2026, 9, 6, 10, 0), _utc(2026, 9, 6, 20, 0), sunday_spec
    )
    assert worked == Decimal("10.00")
    assert status == AttendanceStatus.overtime
    # Same fallback: a normal-length Sunday is just present.
    _, status = service.derive_attendance(
        _utc(2026, 9, 6, 10, 0), _utc(2026, 9, 6, 15, 0), sunday_spec
    )
    assert status == AttendanceStatus.present


def test_schedule_with_no_lines_has_no_baseline():
    """Employee with a schedule that has no lines: no expected hours -> always
    present (no baseline to compare against)."""
    empty_spec = ScheduleSpec(None, None, 0, None)
    _worked, status = service.derive_attendance(
        _utc(2026, 9, 7, 8, 0), _utc(2026, 9, 7, 20, 0), empty_spec
    )
    assert status == AttendanceStatus.present


def test_missing_checkout_boundary_is_eod_plus_grace():
    """Open entry becomes missing only after expected EOD (18:00) + grace
    (default 2h) — an in-progress check-in is not missing during the day."""
    boundary = service.missing_checkout_boundary(
        _utc(2026, 9, 7, 9, 0), FT_SPEC
    )
    assert boundary == _utc(2026, 9, 7, 20, 0)
    before = _utc(2026, 9, 7, 18, 30)
    after = _utc(2026, 9, 7, 21, 0)
    assert before < boundary < after


def test_schema_ordering_validators():
    """Pydantic-layer ordering guards 422 before ever reaching Postgres."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        AttendanceManualCreate(
            employee_id=1,
            check_in=_utc(2026, 9, 7, 18, 0),
            check_out=_utc(2026, 9, 7, 9, 0),  # out before in
        )
    with pytest.raises(ValidationError):
        TimeOffRequestCreate(
            time_off_type_id=1,
            date_from=date(2026, 9, 8),
            date_to=date(2026, 9, 7),  # to before from
            duration=Decimal("1.00"),
        )
    with pytest.raises(ValidationError):
        TimeOffAllocationCreate(
            employee_id=1,
            time_off_type_id=1,
            allocated_amount=Decimal("-2.00"),  # negative grant
            valid_from=date(2026, 1, 1),
            valid_to=date(2025, 12, 31),
        )


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
        try:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        except Exception:
            # pg_trgm backs the employees name-search GIN index — it is not
            # needed by attendance tests, and embedded PG builds (e.g. the
            # local pgserver used to run these tests) ship no contrib modules.
            pass
    # The shared `employees` table declares a gin_trgm_ops GIN index (Ameen's
    # slice; the real DB gets pg_trgm via the initial migration). Fresh-schema
    # fallback runs on PG builds without contrib would fail to create it, so
    # drop that one index from the metadata here — create_all(checkfirst=True)
    # is a no-op on an already-migrated DB anyway, and nothing in this suite
    # searches by trigram similarity.
    from app.models.employee import Employee as _Employee

    _trgm = next(
        (i for i in _Employee.__table__.indexes if i.name == "ix_employees_full_name_trgm"),
        None,
    )
    if _trgm is not None:
        _Employee.__table__.indexes.discard(_trgm)
    # Importing the read-only view mappers (service.py does, to query them)
    # registers their names on Base.metadata — without this, create_all would
    # build real TABLES named v_* and the CREATE OR REPLACE VIEW below would
    # fail with WrongObjectType on a fresh schema.
    from app.models.views import (
        TimeOffBalanceView as _ViewBalance,
        WorkingScheduleHoursView as _ViewHours,
    )

    for _view_table in (_ViewBalance.__table__, _ViewHours.__table__):
        Base.metadata.remove(_view_table)
    Base.metadata.create_all(app_engine)  # idempotent; migration already applied
    with app_engine.begin() as conn:
        conn.execute(text(_TIME_OFF_VIEW_SQL))
        conn.execute(text(_SCHEDULE_VIEW_SQL))
    return app_engine


@pytest.fixture()
def db(db_engine):
    """Per-test rolled-back session: service commits become savepoint
    releases, and the outer transaction is rolled back at teardown."""
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


def _next_monday(base: date | None = None) -> date:
    base = base or date.today()
    return base + timedelta(days=(0 - base.weekday()) % 7)


def _make_employee(db: Session, *, status=EmployeeStatus.active) -> Employee:
    """A full-time employee on a Mon-Fri 09:00-18:00 (1h break) schedule,
    with no contract (contract warnings in tests are expected/ignored)."""
    suffix = uuid.uuid4().hex[:6]
    company = Company(name=f"Co {suffix}", is_active=True)
    db.add(company)
    dept = Department(name=f"Dept {suffix}", company_id=company.id)
    db.add(dept)
    db.flush()
    pos = JobPosition(title=f"Engineer {suffix}", department_id=dept.id)
    db.add(pos)
    sched = WorkingSchedule(
        name=f"Sched {suffix}", schedule_type=ScheduleType.full_time,
        company_id=company.id, is_active=True,
    )
    db.add(sched)
    db.flush()
    for dow in range(5):  # Monday..Friday
        db.add(WorkingScheduleLine(
            working_schedule_id=sched.id, day_of_week=dow,
            start_time=time(9, 0), end_time=time(18, 0), break_minutes=60,
        ))
    emp = Employee(
        full_name=f"Employee {suffix}", work_email=f"emp.{suffix}@test.local",
        department_id=dept.id, job_position_id=pos.id,
        working_schedule_id=sched.id, employee_type=EmployeeType.full_time,
        status=status, date_of_joining=date(2026, 1, 1), company_id=company.id,
    )
    db.add(emp)
    db.flush()
    return emp


_ROLE_CACHE_ATTR = "_pp360_roles"


def _role(db: Session, name: str) -> Role:
    """Session-local Role cache — role names are unique at the DB level, so
    each transaction creates each role once (rolled back with the test)."""
    cache = getattr(db, _ROLE_CACHE_ATTR, None)
    if cache is None:
        cache = {}
        setattr(db, _ROLE_CACHE_ATTR, cache)
    if name not in cache:
        role = Role(name=name, description=f"test role {name}")
        db.add(role)
        db.flush()
        cache[name] = role
    return cache[name]


def _user(db: Session, employee: Employee, role_name: str = "EMPLOYEE") -> User:
    user = User(
        email=f"{uuid.uuid4().hex[:8]}@test.local",
        hashed_password="x", employee_id=employee.id, is_active=True,
    )
    user.roles = [_role(db, role_name)]
    db.add(user)
    db.flush()
    return user


def _hr_user(db: Session, employee: Employee | None = None) -> User:
    user = User(
        email=f"hr.{uuid.uuid4().hex[:8]}@test.local",
        hashed_password="x",
        employee_id=employee.id if employee else None,
        is_active=True,
    )
    user.roles = [_role(db, "HR_MANAGER")]
    db.add(user)
    db.flush()
    return user


def _make_type(
    db: Session,
    name: str | None = None,
    *,
    unit: TimeOffUnit = TimeOffUnit.days,
    requires_allocation: bool = True,
    requires_approval: bool = True,
) -> TimeOffType:
    suffix = uuid.uuid4().hex[:4].upper()
    time_off_type = TimeOffType(
        name=name or f"Type {suffix}", unit=unit,
        requires_allocation=requires_allocation,
        requires_approval=requires_approval,
        affects_payroll=False, company_id=None, is_active=True,
    )
    db.add(time_off_type)
    db.flush()
    return time_off_type


def _future(days: int) -> date:
    return date.today() + timedelta(days=days)


def _check_in_open(db: Session, employee_id: int) -> Attendance:
    return db.scalar(
        select(Attendance).where(
            Attendance.employee_id == employee_id,
            Attendance.check_out.is_(None),
        )
    )


# -- attendance flows --------------------------------------------------------


def test_check_in_check_out_flow_computes_hours_and_status(db, monkeypatch):
    emp = _make_employee(db)
    employee_user = _user(db, emp)

    monday = _next_monday()
    monday_9 = datetime.combine(monday, time(9, 0), tzinfo=UTC)
    monday_18 = datetime.combine(monday, time(18, 0), tzinfo=UTC)

    entry = service.check_in(
        db, employee_user, AttendanceCheckInCreate(check_in=monday_9)
    )
    assert entry.check_out is None
    assert entry.worked_hours is None
    assert _check_in_open(db, emp.id) is not None

    # check_out uses server now() — pin it so the math is deterministic.
    monkeypatch.setattr(service, "_utcnow", lambda: monday_18)
    closed = service.check_out(db, employee_user, entry.id)
    assert closed.check_out == monday_18
    assert closed.worked_hours == Decimal("8.00")  # 9h - 1h break
    assert closed.status == AttendanceStatus.present

    # Employee's own list sees exactly this row; HR sees it too.
    mine = service.list_attendance(db, employee_user)
    assert mine.total == 1 and mine.items[0].employee_id == emp.id
    assert mine.items[0].employee_name == emp.full_name


def test_overnight_check_in_out_flow(db, monkeypatch):
    emp = _make_employee(db)
    employee_user = _user(db, emp)

    monday = _next_monday()
    entry = service.check_in(
        db, employee_user,
        AttendanceCheckInCreate(check_in=datetime.combine(
            monday, time(23, 50), tzinfo=UTC)),
    )
    # Overnight: checkout crosses into the next calendar day.
    monkeypatch.setattr(
        service, "_utcnow",
        lambda: datetime.combine(monday + timedelta(days=1), time(0, 20), tzinfo=UTC),
    )
    closed = service.check_out(db, employee_user, entry.id)
    assert closed.worked_hours == Decimal("0.50")
    assert closed.check_out.date() == monday + timedelta(days=1)


def test_double_check_in_without_checkout_is_conflict(db):
    emp = _make_employee(db)
    employee_user = _user(db, emp)
    service.check_in(db, employee_user, AttendanceCheckInCreate())
    with pytest.raises(ConflictException):
        service.check_in(db, employee_user, AttendanceCheckInCreate())


def test_check_out_with_no_open_entry_is_conflict(db):
    emp = _make_employee(db)
    hr = _hr_user(db)
    closed = service.create_manual_attendance(
        db, hr,
        AttendanceManualCreate(
            employee_id=emp.id,
            check_in=datetime.combine(_next_monday(), time(9, 0), tzinfo=UTC),
            check_out=datetime.combine(_next_monday(), time(18, 0), tzinfo=UTC),
        ),
    )
    with pytest.raises(ConflictException):  # already checked out
        service.check_out(db, hr, closed.id)
    with pytest.raises(ConflictException):  # double close, idempotency
        service.check_out(db, hr, closed.id)


def test_employee_cannot_backdate_for_terminated_colleague(db):
    """EMPLOYEE cannot check in for themselves once terminated, and HR back-
    filling a terminated employee (historical) is explicitly allowed."""
    term = _make_employee(db, status=EmployeeStatus.terminated)
    active = _make_employee(db)
    term_owner = _user(db, term)
    active_user = _user(db, active)

    with pytest.raises(ForbiddenException):
        service.check_in(db, term_owner, AttendanceCheckInCreate())
    with pytest.raises(ForbiddenException):
        # An active employee must never target anyone else's row either.
        service.check_in(
            db, active_user,
            AttendanceCheckInCreate(employee_id=term.id),
        )

    hr = _hr_user(db)
    backfill = service.check_in(
        db, hr, AttendanceCheckInCreate(employee_id=term.id)
    )
    assert backfill.employee_id == term.id


def test_manual_entry_and_patch_stamp_manual_correction(db):
    emp = _make_employee(db)
    hr = _hr_user(db)

    manual = service.create_manual_attendance(
        db, hr,
        AttendanceManualCreate(
            employee_id=emp.id,
            check_in=datetime.combine(_next_monday(), time(9, 20), tzinfo=UTC),
            check_out=datetime.combine(_next_monday(), time(18, 0), tzinfo=UTC),
        ),
    )
    assert manual.is_manual_correction is True
    assert manual.corrected_by_user_id == hr.id
    assert manual.status == AttendanceStatus.late

    # HR corrects the late check-in to on-time -> status recomputed.
    from app.schemas.attendance_timeoff import AttendanceUpdate

    fixed = service.update_attendance(
        db, hr, manual.id,
        AttendanceUpdate(
            check_in=datetime.combine(_next_monday(), time(9, 0), tzinfo=UTC)
        ),
    )
    assert fixed.status == AttendanceStatus.present
    assert fixed.is_manual_correction is True
    assert fixed.corrected_by_user_id == hr.id


def test_summary_counts_and_derives_absent_from_schedule(db):
    emp = _make_employee(db)
    hr = _hr_user(db)
    monday = _next_monday()
    # Attend two weekdays (Mon/Tue) of a Mon-Fri week.
    for i, dow in enumerate((0, 1)):
        day = monday + timedelta(days=i)
        service.create_manual_attendance(
            db, hr,
            AttendanceManualCreate(
                employee_id=emp.id,
                check_in=datetime.combine(day, time(9, 0), tzinfo=UTC),
                check_out=datetime.combine(day, time(18, 0), tzinfo=UTC),
            ),
        )
    summary = service.get_attendance_summary(db, hr, emp.id, monday, monday + timedelta(days=6))
    assert summary.expected_workdays == 5
    assert summary.present == 2
    assert summary.absent == 3  # expected 5 - attended 2 (no synthetic rows)


# -- time off allocations / balances ----------------------------------------


def test_allocation_approved_balance_then_request_beyond_remaining(db):
    emp = _make_employee(db)
    employee_user = _user(db, emp)
    hr = _hr_user(db)
    pto = _make_type(db, "PTO", unit=TimeOffUnit.days, requires_allocation=True)

    alloc = service.create_time_off_allocation(
        db, hr,
        TimeOffAllocationCreate(
            employee_id=emp.id, time_off_type_id=pto.id,
            allocated_amount=Decimal("10.00"),
            valid_from=_future(-5), valid_to=None,
        ),
    )
    assert alloc.status == AllocationStatus.to_approve

    # to_approve allocation must not count toward balance yet.
    assert service.remaining_balance(db, emp.id, pto.id) == 0

    approved = service.approve_time_off_allocation(db, hr, alloc.id)
    assert approved.status == AllocationStatus.approved
    assert approved.approver_id == hr.employee_id

    balances = {
        b.type_name: b
        for b in service.list_time_off_balances(db, employee_id=emp.id)
    }
    assert balances["PTO"].allocated == Decimal("10.00")
    assert balances["PTO"].remaining == Decimal("10.00")

    # Employee requests 6 days (future, non-overlapping with nothing else).
    req = service.create_time_off_request(
        db, employee_user,
        TimeOffRequestCreate(
            time_off_type_id=pto.id, date_from=_future(10),
            date_to=_future(15), duration=Decimal("6.00"), reason="Holiday",
        ),
    )
    approved_req = service.approve_time_off_request(db, hr, req.id)
    assert approved_req.status == TimeOffRequestStatus.approved
    assert service.remaining_balance(db, emp.id, pto.id) == Decimal("4.00")

    # Asking for more than the remaining 4 days -> 409, never negative.
    over = service.create_time_off_request(
        db, employee_user,
        TimeOffRequestCreate(
            time_off_type_id=pto.id, date_from=_future(40),
            date_to=_future(44), duration=Decimal("5.00"), reason="Too long",
        ),
    )
    with pytest.raises(ConflictException) as excinfo:
        service.approve_time_off_request(db, hr, over.id)
    assert "requested" in str(excinfo.value)
    assert service.remaining_balance(db, emp.id, pto.id) == Decimal("4.00")


def test_allocation_state_machine_and_double_approve(db):
    emp = _make_employee(db)
    hr = _hr_user(db)
    pto = _make_type(db, "Sick", unit=TimeOffUnit.days)
    alloc = service.create_time_off_allocation(
        db, hr,
        TimeOffAllocationCreate(
            employee_id=emp.id, time_off_type_id=pto.id,
            allocated_amount=Decimal("5.00"),
            valid_from=_future(-5), valid_to=None,
        ),
    )
    service.approve_time_off_allocation(db, hr, alloc.id)
    with pytest.raises(ConflictException):  # already approved -> idempotency guard
        service.approve_time_off_allocation(db, hr, alloc.id)

    other = service.create_time_off_allocation(
        db, hr,
        TimeOffAllocationCreate(
            employee_id=emp.id, time_off_type_id=pto.id,
            allocated_amount=Decimal("3.00"),
            valid_from=_future(-5), valid_to=None,
        ),
    )
    refused = service.refuse_time_off_allocation(db, hr, other.id)
    assert refused.status == AllocationStatus.refused
    with pytest.raises(ConflictException):  # terminal state
        service.approve_time_off_allocation(db, hr, other.id)


def test_expired_allocation_visible_in_history_but_not_in_balance(db):
    emp = _make_employee(db)
    hr = _hr_user(db)
    pto = _make_type(db, "Expired", unit=TimeOffUnit.days)
    alloc = service.create_time_off_allocation(
        db, hr,
        TimeOffAllocationCreate(
            employee_id=emp.id, time_off_type_id=pto.id,
            allocated_amount=Decimal("10.00"),
            valid_from=_future(-60), valid_to=_future(-1),  # already lapsed
        ),
    )
    service.approve_time_off_allocation(db, hr, alloc.id)
    # Historical/audit view still lists it...
    assert service.list_time_off_allocations(
        db, employee_id=emp.id, status=AllocationStatus.approved
    ).total == 1
    # ...but it does not count toward new-request eligibility.
    assert service.remaining_balance(db, emp.id, pto.id) == 0


# -- time off requests -------------------------------------------------------


def _unpaid_type(db) -> TimeOffType:
    return _make_type(db, "Unpaid", unit=TimeOffUnit.days,
                      requires_allocation=False)


def test_approving_two_overlapping_requests_blocks_second(db):
    emp = _make_employee(db)
    employee_user = _user(db, emp)
    hr = _hr_user(db)
    unpaid = _unpaid_type(db)

    first = service.create_time_off_request(
        db, employee_user,
        TimeOffRequestCreate(
            time_off_type_id=unpaid.id, date_from=_future(10),
            date_to=_future(12), duration=Decimal("3.00"),
        ),
    )
    second = service.create_time_off_request(
        db, employee_user,
        TimeOffRequestCreate(
            time_off_type_id=unpaid.id, date_from=_future(12),  # shares a day
            date_to=_future(14), duration=Decimal("3.00"),
        ),
    )
    # Two to_approve requests may coexist (nothing approved yet)...
    assert first.status == TimeOffRequestStatus.to_approve
    assert second.status == TimeOffRequestStatus.to_approve

    service.approve_time_off_request(db, hr, first.id)
    with pytest.raises(ConflictException) as excinfo:
        service.approve_time_off_request(db, hr, second.id)
    assert "overlap" in str(excinfo.value).lower()


def test_request_creation_blocked_by_existing_approved_overlap(db):
    emp = _make_employee(db)
    employee_user = _user(db, emp)
    hr = _hr_user(db)
    unpaid = _unpaid_type(db)

    approved = service.create_time_off_request(
        db, employee_user,
        TimeOffRequestCreate(
            time_off_type_id=unpaid.id, date_from=_future(10),
            date_to=_future(12), duration=Decimal("3.00"),
        ),
    )
    service.approve_time_off_request(db, hr, approved.id)

    with pytest.raises(ConflictException):
        service.create_time_off_request(
            db, employee_user,
            TimeOffRequestCreate(
                time_off_type_id=unpaid.id, date_from=_future(12),
                date_to=_future(15), duration=Decimal("4.00"),
            ),
        )


def test_refuse_and_double_decision_on_approved_request(db):
    emp = _make_employee(db)
    employee_user = _user(db, emp)
    hr = _hr_user(db)
    unpaid = _unpaid_type(db)

    req = service.create_time_off_request(
        db, employee_user,
        TimeOffRequestCreate(
            time_off_type_id=unpaid.id, date_from=_future(20),
            date_to=_future(21), duration=Decimal("2.00"),
        ),
    )
    service.approve_time_off_request(db, hr, req.id)
    # Terminal: neither refuse nor approve may revisit the decision.
    with pytest.raises(ConflictException):
        service.refuse_time_off_request(db, hr, req.id)
    with pytest.raises(ConflictException):
        service.approve_time_off_request(db, hr, req.id)


def test_cancel_approved_request_before_start_ok_after_start_blocked(db):
    emp = _make_employee(db)
    employee_user = _user(db, emp)
    hr = _hr_user(db)
    unpaid = _unpaid_type(db)

    def _approved(days_from_now: int) -> TimeOffRequest:
        req = service.create_time_off_request(
            db, employee_user,
            TimeOffRequestCreate(
                time_off_type_id=unpaid.id, date_from=_future(days_from_now),
                date_to=_future(days_from_now + 1), duration=Decimal("2.00"),
            ),
        )
        service.approve_time_off_request(db, hr, req.id)
        return req

    pre_start = _approved(30)  # leave starts in the future
    assert service.cancel_time_off_request(db, hr, pre_start.id).status \
        == TimeOffRequestStatus.cancelled

    started = _approved(-10)  # leave already started
    with pytest.raises(ConflictException):
        service.cancel_time_off_request(db, hr, started.id)


def test_cancel_rules_for_requester_and_others(db):
    emp = _make_employee(db)
    other = _make_employee(db)
    employee_user = _user(db, emp)
    other_user = _user(db, other)
    hr = _hr_user(db)
    unpaid = _unpaid_type(db)

    req = service.create_time_off_request(
        db, employee_user,
        TimeOffRequestCreate(
            time_off_type_id=unpaid.id, date_from=_future(5),
            date_to=_future(6), duration=Decimal("2.00"),
        ),
    )
    # Owner cancels their own pending request.
    assert service.cancel_time_off_request(db, employee_user, req.id).status \
        == TimeOffRequestStatus.cancelled

    other_req = service.create_time_off_request(
        db, employee_user,
        TimeOffRequestCreate(
            time_off_type_id=unpaid.id, date_from=_future(8),
            date_to=_future(9), duration=Decimal("2.00"),
        ),
    )
    # A different EMPLOYEE can't cancel someone else's request.
    with pytest.raises(ForbiddenException):
        service.cancel_time_off_request(db, other_user, other_req.id)
    # Nor can the requester cancel their own APPROVED leave (HR only).
    service.approve_time_off_request(db, hr, other_req.id)
    with pytest.raises(ForbiddenException):
        service.cancel_time_off_request(db, employee_user, other_req.id)


def test_employee_never_sees_another_employees_records(db):
    emp_a = _make_employee(db)
    emp_b = _make_employee(db)
    user_a = _user(db, emp_a)
    user_b = _user(db, emp_b)  # one account per employee (unique employee_id)
    hr = _hr_user(db)

    monday = _next_monday()
    b_att = service.create_manual_attendance(
        db, hr,
        AttendanceManualCreate(
            employee_id=emp_b.id,
            check_in=datetime.combine(monday, time(9, 0), tzinfo=UTC),
            check_out=datetime.combine(monday, time(18, 0), tzinfo=UTC),
        ),
    )
    unpaid = _unpaid_type(db)
    b_req = service.create_time_off_request(
        db, user_b,
        TimeOffRequestCreate(
            time_off_type_id=unpaid.id, date_from=_future(10),
            date_to=_future(11), duration=Decimal("2.00"),
        ),
    )

    # Listing with another employee_id -> 403, never a scoped-to-them result.
    with pytest.raises(ForbiddenException):
        service.list_attendance(db, user_a, employee_id=emp_b.id)
    with pytest.raises(ForbiddenException):
        service.list_time_off_requests(db, user_a, employee_id=emp_b.id)
    with pytest.raises(ForbiddenException):
        service.get_attendance(db, user_a, b_att.id)
    with pytest.raises(ForbiddenException):
        service.get_time_off_request(db, user_a, b_req.id)
    with pytest.raises(ForbiddenException):
        service.get_attendance_summary(db, user_a, emp_b.id)
    with pytest.raises(ForbiddenException):
        service.create_time_off_request(
            db, user_a,
            TimeOffRequestCreate(
                time_off_type_id=unpaid.id, date_from=_future(20),
                date_to=_future(21), duration=Decimal("2.00"),
                employee_id=emp_b.id,
            ),
        )

    # Un-scoped listings are forced to A's own rows (empty, never B's).
    assert service.list_attendance(db, user_a).total == 0
    assert service.list_time_off_requests(db, user_a).total == 0

    # HR sees across employees without an employee_id filter.
    assert service.list_attendance(db, hr).total == 1
    assert service.list_time_off_requests(db, hr).total == 1

    # Balances self-service only shows A's own types.
    service.create_time_off_allocation(
        db, hr,
        TimeOffAllocationCreate(
            employee_id=emp_b.id, time_off_type_id=unpaid.id,
            allocated_amount=Decimal("5.00"),
            valid_from=_future(-5), valid_to=None,
        ),
    )
    own_balances = service.get_my_balances(db, user_a)
    assert all(b.employee_id == emp_a.id for b in own_balances)


def test_balances_me_and_hr_employee_filter(db):
    emp = _make_employee(db)
    hr = _hr_user(db)
    employee_user = _user(db, emp)
    pto = _make_type(db, "PTO", unit=TimeOffUnit.days)
    alloc = service.create_time_off_allocation(
        db, hr,
        TimeOffAllocationCreate(
            employee_id=emp.id, time_off_type_id=pto.id,
            allocated_amount=Decimal("10.00"),
            valid_from=_future(-5), valid_to=None,
        ),
    )
    service.approve_time_off_allocation(db, hr, alloc.id)
    employee_balance = service.get_my_balances(db, employee_user)
    assert len(employee_balance) == 1
    assert employee_balance[0].remaining == Decimal("10.00")
    assert employee_balance[0].type_name == "PTO"
    # HR filters by employee.
    hr_view = service.list_time_off_balances(db, employee_id=emp.id)
    assert [b.employee_id for b in hr_view] == [emp.id]


# ===========================================================================
# RBAC via the HTTP API (mirrors Steve's payroll HTTP gate tests)
# ===========================================================================


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


def test_rbac_gates_attendance_and_time_off(db):
    """EMPLOYEE: read-only own-scope, 403 on every HR surface. HR_MANAGER:
    200 on the management list endpoints + can create time off types."""
    created_users: list[User] = []
    created_types: list[int] = []
    try:
        employee = _make_api_user(["EMPLOYEE"])
        hr_manager = _make_api_user(["HR_MANAGER"])
        created_users = [employee, hr_manager]

        client = TestClient(app)

        def auth(user: User) -> dict:
            return {"Authorization": f"Bearer {create_access_token(user.id)}"}

        # Everyone may read time off types (request-form dropdown).
        r = client.get("/api/v1/time-off/types", headers=auth(employee))
        assert r.status_code == 200, r.text

        # EMPLOYEE is locked out of every HR-only surface (dependency gate).
        # Bodies are always syntactically valid so the 403 comes from the role
        # gate, not from a coincidental 422 validation failure.
        type_payload = {
            "name": f"EmployeeBlocked{uuid.uuid4().hex[:4]}",
            "unit": "days",
            "requires_allocation": True,
            "requires_approval": True,
            "affects_payroll": False,
            "is_active": True,
        }
        manual_payload = {
            "employee_id": 1,
            "check_in": "2026-09-07T09:00:00Z",
            "check_out": "2026-09-07T18:00:00Z",
        }
        gate_checks = [
            ("GET", "/api/v1/time-off/allocations", None),
            ("GET", "/api/v1/time-off/balances", None),
            ("POST", "/api/v1/time-off/types", type_payload),
            ("POST", "/api/v1/attendance", manual_payload),
            ("PATCH", "/api/v1/attendance/1", {}),
            ("POST", "/api/v1/time-off/requests/1/approve", None),
            ("POST", "/api/v1/time-off/requests/1/refuse", None),
            ("POST", "/api/v1/time-off/allocations/1/approve", None),
            ("POST", "/api/v1/time-off/allocations/1/refuse", None),
            ("POST", "/api/v1/attendance/sweep-missing-checkouts", None),
        ]
        for method, path, payload in gate_checks:
            r = client.request(method, path, headers=auth(employee), json=payload)
            assert r.status_code == 403, (method, path, r.text)

        # HR_MANAGER sees the management surfaces (full CRUD on this module).
        r = client.get("/api/v1/attendance", headers=auth(hr_manager))
        assert r.status_code == 200, r.text
        r = client.get("/api/v1/time-off/allocations", headers=auth(hr_manager))
        assert r.status_code == 200, r.text
        r = client.get("/api/v1/time-off/balances", headers=auth(hr_manager))
        assert r.status_code == 200, r.text

        payload = {
            "name": f"HRCreates{uuid.uuid4().hex[:4].upper()}",
            "unit": "days",
            "requires_allocation": True,
            "requires_approval": True,
            "affects_payroll": False,
            "is_active": True,
        }
        r = client.post("/api/v1/time-off/types", headers=auth(hr_manager), json=payload)
        assert r.status_code == 201, r.text
        created_types.append(r.json()["id"])

        # EMPLOYEE can still open a check-in for themselves over HTTP.
        emp = employee  # no linked employee row in this test DB -> 404, not 403
        r = client.post("/api/v1/attendance/check-in", headers=auth(emp), json={})
        assert r.status_code == 404, r.text  # "No employee is linked..."
    finally:
        from app.core.database import SessionLocal

        with SessionLocal() as db:
            if created_types:
                db.execute(
                    TimeOffType.__table__.delete().where(
                        TimeOffType.id.in_(created_types)
                    )
                )
            if created_users:
                db.execute(
                    User.__table__.delete().where(
                        User.id.in_([u.id for u in created_users])
                    )
                )
            db.commit()
