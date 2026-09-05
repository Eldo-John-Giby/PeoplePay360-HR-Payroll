"""Employee module tests (OWNER: Ameen) — pytest + httpx AsyncClient.

Runs against a real Postgres (preferred by the spec over SQLite so the
partial unique index `uq_contracts_one_running_per_employee` and the DB CHECK
constraints are actually exercised). Point TEST_DATABASE_URL at any Postgres,
e.g. the throwaway container:

    docker run -d --name peoplepay-test-db \
      -e POSTGRES_USER=peoplepay -e POSTGRES_PASSWORD=peoplepay \
      -e POSTGRES_DB=peoplepay_test -p 5433:5432 postgres:16-alpine
    TEST_DATABASE_URL=postgresql+psycopg2://peoplepay:peoplepay@localhost:5433/peoplepay_test \
      pytest tests/test_employees.py

Coverage: every §2 edge case has at least one test below; anything not
covered is called out in the module PR description.
"""

import os
from datetime import date, time
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from app.core.base import Base
from app.core.database import get_db
from app.core.security import create_access_token, hash_password
from app.main import app
from app.models import (
    Attendance,
    Company,
    Contract,
    Department,
    Employee,
    JobPosition,
    Role,
    SalaryStructure,
    TimeOffAllocation,
    TimeOffRequest,
    TimeOffType,
    User,
    WorkingSchedule,
    WorkingScheduleLine,
)
from app.models.enums import (
    AllocationStatus,
    AttendanceStatus,
    ContractStatus,
    EmployeeStatus,
    ScheduleType,
    TimeOffRequestStatus,
    TimeOffUnit,
)
from app.modules.employees.service import compute_total_weekly_hours

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+psycopg2://peoplepay:peoplepay@localhost:5433/peoplepay_test",
)

engine = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
TestSession = sessionmaker(
    bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
)

PASSWORD = "Password@123"
ROLE_NAMES = [
    "EMPLOYEE", "HR_MANAGER", "HR_PAYROLL_USER", "HR_PAYROLL_MANAGER", "ADMIN",
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def anyio_backend():
    """Run @pytest.mark.anyio tests on asyncio only (trio isn't installed)."""
    return "asyncio"


@pytest.fixture(scope="session", autouse=True)
def _schema():
    """Create the pg_trgm extension + full schema once per session."""
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield


@pytest.fixture(autouse=True)
def _clean_tables(_schema):
    """Wipe all rows (and reset identity sequences) between tests."""
    with engine.begin() as conn:
        names = ", ".join(t.name for t in Base.metadata.sorted_tables)
        conn.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
    yield


@pytest.fixture
def db(_clean_tables):
    """Direct DB session for test setup / assertions."""
    session = TestSession()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
async def client(db):
    """AsyncClient against the real app with get_db pointed at the test DB.

    httpx >= 0.28 dropped the context-manager protocol on AsyncClient, so
    the client is closed explicitly.
    """

    def override_get_db():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://test")
    try:
        yield client
    finally:
        app.dependency_overrides.clear()
        await client.aclose()


@pytest.fixture
def roles(db):
    created = {}
    for name in ROLE_NAMES:
        role = Role(name=name, description=name)
        db.add(role)
        created[name] = role
    db.commit()
    return created


@pytest.fixture
def master(db):
    """Company + department + job position + 5-day schedule + salary structure."""
    company = Company(name="OXP", is_active=True)
    db.add(company)
    db.flush()
    dept = Department(name="Engineering", company_id=company.id, is_active=True)
    db.add(dept)
    db.flush()
    pos = JobPosition(title="Engineer", department_id=dept.id, is_active=True)
    db.add(pos)
    db.flush()
    sched = WorkingSchedule(
        name="Full-Time", schedule_type=ScheduleType.full_time,
        company_id=company.id, is_active=True,
    )
    db.add(sched)
    db.flush()
    for dow in range(5):
        db.add(
            WorkingScheduleLine(
                working_schedule_id=sched.id, day_of_week=dow,
                start_time=time(9, 0), end_time=time(18, 0), break_minutes=60,
            )
        )
    structure = SalaryStructure(
        name="Regular", code="REG", company_id=company.id, is_active=True,
    )
    db.add(structure)
    db.commit()
    return SimpleNamespace(
        company=company, dept=dept, pos=pos, sched=sched, structure=structure,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_user(db, email, role_list, employee_id=None):
    user = User(
        email=email,
        hashed_password=hash_password(PASSWORD),
        employee_id=employee_id,
        is_active=True,
    )
    user.roles = role_list
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def auth(user_id: int) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


async def create_employee(client, headers, master, **overrides):
    payload = {
        "full_name": "Test Employee",
        "work_email": "test.employee@oxp.com",
        "department_id": master.dept.id,
        "job_position_id": master.pos.id,
        "working_schedule_id": master.sched.id,
        "employee_type": "full_time",
        "status": "active",
        "date_of_joining": "2024-01-15",
    }
    payload.update(overrides)
    return await client.post("/api/v1/employees", json=payload, headers=headers)


async def create_contract(client, headers, master, employee_id,
                          start="2024-01-15", end=None, wage="100000.00"):
    payload = {
        "employee_id": employee_id,
        "department_id": master.dept.id,
        "job_position_id": master.pos.id,
        "working_schedule_id": master.sched.id,
        "salary_structure_id": master.structure.id,
        "wage_monthly": wage,
        "start_date": start,
    }
    if end:
        payload["end_date"] = end
    resp = await client.post(
        "/api/v1/contracts", json=payload, headers=headers
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def activate(client, headers, contract_id, body=None):
    return await client.post(
        f"/api/v1/contracts/{contract_id}/activate",
        json=body or {}, headers=headers,
    )


# ---------------------------------------------------------------------------
# Spec §4 minimum coverage
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_create_department_job_position_employee_chain(
    client, master, roles, db
):
    """§4.1: department -> job position -> employee = 201 chain."""
    hr = make_user(db, "hr@oxp.com", [roles["HR_MANAGER"]])
    h = auth(hr.id)

    r = await client.post(
        "/api/v1/departments",
        json={"name": "Sales", "company_id": master.company.id}, headers=h,
    )
    assert r.status_code == 201
    dept_id = r.json()["id"]

    r = await client.post(
        "/api/v1/job-positions",
        json={"title": "Sales Executive", "department_id": dept_id}, headers=h,
    )
    assert r.status_code == 201
    pos_id = r.json()["id"]

    r = await create_employee(
        client, h, master,
        full_name="Riya Kapoor",
        work_email="riya.kapoor@oxp.com",
        department_id=dept_id, job_position_id=pos_id,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["full_name"] == "Riya Kapoor"
    assert body["department"]["name"] == "Sales"
    assert body["job_position"]["title"] == "Sales Executive"
    assert body["related"] == {
        "contracts_count": 0, "attendance_count": 0,
        "time_off_count": 0, "allocations_count": 0,
    }


@pytest.mark.anyio
async def test_duplicate_work_email_conflict(client, master, roles, db):
    """§4.2: duplicate work_email -> 409 with a clear message (not a 500)."""
    hr = make_user(db, "hr@oxp.com", [roles["HR_MANAGER"]])
    h = auth(hr.id)

    r1 = await create_employee(client, h, master, work_email="dup@oxp.com")
    assert r1.status_code == 201

    r2 = await create_employee(
        client, h, master, full_name="Other Person", work_email="dup@oxp.com"
    )
    assert r2.status_code == 409
    assert "already exists" in r2.json()["detail"]

    # Case-insensitive: uppercase must also collide (app-level lowercasing).
    r3 = await create_employee(
        client, h, master, full_name="Third Person", work_email="DUP@OXP.COM"
    )
    assert r3.status_code == 409


@pytest.mark.anyio
async def test_activate_second_contract_expires_first(
    client, master, roles, db
):
    """§4.3: two drafts, activate first then second -> first expired (with
    end_date = second.start - 1), second running, no raw constraint error."""
    hr = make_user(db, "hr@oxp.com", [roles["HR_MANAGER"]])
    h = auth(hr.id)
    emp = (await create_employee(client, h, master)).json()

    c1 = await create_contract(client, h, master, emp["id"], start="2024-01-15")
    c2 = await create_contract(client, h, master, emp["id"], start="2025-01-15")

    r1 = await activate(client, h, c1["id"])
    assert r1.status_code == 200, r1.text
    assert r1.json()["status"] == "running"

    r2 = await activate(client, h, c2["id"])
    assert r2.status_code == 200, r2.text
    assert r2.json()["status"] == "running"

    g1 = await client.get(f"/api/v1/contracts/{c1['id']}", headers=h)
    assert g1.status_code == 200
    assert g1.json()["status"] == "expired"
    assert g1.json()["end_date"] == "2025-01-14"  # no gap, no overlap

    # Exactly one running contract remains — the DB index is satisfied.
    running = db.scalars(
        select(Contract).where(
            Contract.employee_id == emp["id"],
            Contract.status == ContractStatus.running,
        )
    ).all()
    assert len(running) == 1


@pytest.mark.anyio
async def test_activate_out_of_order_rejected(client, master, roles, db):
    """§4.4: activating a second contract with an EARLIER start_date -> 422."""
    hr = make_user(db, "hr@oxp.com", [roles["HR_MANAGER"]])
    h = auth(hr.id)
    emp = (await create_employee(client, h, master)).json()

    c1 = await create_contract(client, h, master, emp["id"], start="2025-01-15")
    await activate(client, h, c1["id"])

    c2 = await create_contract(client, h, master, emp["id"], start="2024-06-01")
    r = await activate(client, h, c2["id"])
    assert r.status_code == 422
    assert "chronological" in r.json()["detail"]

    # Equal start dates are also out of order (would break the date-range rule).
    c3 = await create_contract(client, h, master, emp["id"], start="2025-01-15")
    r3 = await activate(client, h, c3["id"])
    assert r3.status_code == 422


@pytest.mark.anyio
async def test_employee_role_cannot_list_but_can_read_me(
    client, master, roles, db
):
    """§4.5: EMPLOYEE hitting GET /employees -> 403; GET /employees/me -> 200."""
    hr = make_user(db, "hr@oxp.com", [roles["HR_MANAGER"]])
    h = auth(hr.id)
    emp = (await create_employee(client, h, master)).json()

    emp_user = make_user(
        db, "emp@oxp.com", [roles["EMPLOYEE"]], employee_id=emp["id"]
    )
    eh = auth(emp_user.id)

    r = await client.get("/api/v1/employees", headers=eh)
    assert r.status_code == 403

    r = await client.get("/api/v1/employees/me", headers=eh)
    assert r.status_code == 200
    assert r.json()["id"] == emp["id"]

    # No linked employee -> 404.
    ghost = make_user(db, "ghost@oxp.com", [roles["EMPLOYEE"]])
    r = await client.get("/api/v1/employees/me", headers=auth(ghost.id))
    assert r.status_code == 404


@pytest.mark.anyio
async def test_schedule_overlapping_lines_rejected(client, roles, db):
    """§4.6: overlapping lines on the same day -> 422 (friendly message)."""
    hr = make_user(db, "hr@oxp.com", [roles["HR_MANAGER"]])
    h = auth(hr.id)

    r = await client.post(
        "/api/v1/working-schedules",
        json={
            "name": "Bad Schedule", "schedule_type": "full_time",
            "lines": [
                {"day_of_week": 0, "start_time": "09:00",
                 "end_time": "13:00", "break_minutes": 0},
                {"day_of_week": 0, "start_time": "12:00",
                 "end_time": "17:00", "break_minutes": 0},
            ],
        },
        headers=h,
    )
    assert r.status_code == 422
    assert "overlap" in r.json()["detail"].lower()

    # Contiguous ranges (09-13 then 13-17) are NOT an overlap.
    r = await client.post(
        "/api/v1/working-schedules",
        json={
            "name": "Contiguous", "schedule_type": "custom",
            "lines": [
                {"day_of_week": 0, "start_time": "09:00",
                 "end_time": "13:00", "break_minutes": 0},
                {"day_of_week": 0, "start_time": "13:00",
                 "end_time": "17:00", "break_minutes": 0},
            ],
        },
        headers=h,
    )
    assert r.status_code == 201, r.text


@pytest.mark.anyio
async def test_department_self_cycle_rejected(client, master, roles, db):
    """§4.7: parent_department_id pointing at itself -> 422."""
    hr = make_user(db, "hr@oxp.com", [roles["HR_MANAGER"]])
    h = auth(hr.id)
    dept = (
        await client.post(
            "/api/v1/departments",
            json={"name": "Eng", "company_id": master.company.id}, headers=h,
        )
    ).json()

    r = await client.patch(
        f"/api/v1/departments/{dept['id']}",
        json={"parent_department_id": dept["id"]}, headers=h,
    )
    assert r.status_code == 422

    # Two-hop cycle A -> B -> A is also rejected.
    b = (
        await client.post(
            "/api/v1/departments",
            json={"name": "Sub", "parent_department_id": dept["id"]}, headers=h,
        )
    ).json()
    r = await client.patch(
        f"/api/v1/departments/{dept['id']}",
        json={"parent_department_id": b["id"]}, headers=h,
    )
    assert r.status_code == 422


@pytest.mark.anyio
async def test_soft_delete_department_with_active_employees_conflict(
    client, master, roles, db
):
    """§4.8: soft-deleting a department with active employees -> 409 with
    counts in the message (never a silent cascade)."""
    hr = make_user(db, "hr@oxp.com", [roles["HR_MANAGER"]])
    h = auth(hr.id)
    dept_id = master.dept.id

    await create_employee(client, h, master)  # active employee in master dept

    r = await client.delete(f"/api/v1/departments/{dept_id}", headers=h)
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert "1 active employee" in detail
    assert "1 active job position" in detail

    # An empty department deletes fine (204).
    empty = (
        await client.post(
            "/api/v1/departments",
            json={"name": "Empty", "company_id": master.company.id}, headers=h,
        )
    ).json()
    r = await client.delete(f"/api/v1/departments/{empty['id']}", headers=h)
    assert r.status_code == 204
    r = await client.get(f"/api/v1/departments/{empty['id']}", headers=h)
    assert r.json()["is_active"] is False


# ---------------------------------------------------------------------------
# Contract edge cases (§2.5)
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_patch_running_contract_wage_rejected(client, master, roles, db):
    """Changing wage on a running contract -> 409 (history preserved instead:
    new draft + activate)."""
    hr = make_user(db, "hr@oxp.com", [roles["HR_MANAGER"]])
    h = auth(hr.id)
    emp = (await create_employee(client, h, master)).json()
    c = await create_contract(client, h, master, emp["id"])
    await activate(client, h, c["id"])

    r = await client.patch(
        f"/api/v1/contracts/{c['id']}",
        json={"wage_monthly": "200000.00"}, headers=h,
    )
    assert r.status_code == 409
    assert "draft" in r.json()["detail"]


@pytest.mark.anyio
async def test_contract_optimistic_lock_stale_version(client, master, roles, db):
    """§2.5: stale version_id -> 409; correct version applies and bumps it."""
    hr = make_user(db, "hr@oxp.com", [roles["HR_MANAGER"]])
    h = auth(hr.id)
    emp = (await create_employee(client, h, master)).json()
    c = await create_contract(client, h, master, emp["id"])
    assert c["version_id"] == 1

    r = await client.patch(
        f"/api/v1/contracts/{c['id']}",
        json={"wage_monthly": "90000.00", "version_id": 99}, headers=h,
    )
    assert r.status_code == 409
    assert "modified" in r.json()["detail"]

    r = await client.patch(
        f"/api/v1/contracts/{c['id']}",
        json={"wage_monthly": "90000.00", "version_id": 1}, headers=h,
    )
    assert r.status_code == 200
    assert r.json()["wage_monthly"] == "90000.00"
    assert r.json()["version_id"] == 2


@pytest.mark.anyio
async def test_expire_sets_end_date_today(client, master, roles, db):
    hr = make_user(db, "hr@oxp.com", [roles["HR_MANAGER"]])
    h = auth(hr.id)
    emp = (await create_employee(client, h, master)).json()
    c = await create_contract(client, h, master, emp["id"], start="2024-01-15")
    await activate(client, h, c["id"])

    r = await client.post(
        f"/api/v1/contracts/{c['id']}/expire", json={}, headers=h
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "expired"
    assert body["end_date"] == date.today().isoformat()

    # Idempotent-guard: expiring an expired contract -> 409.
    r2 = await client.post(
        f"/api/v1/contracts/{c['id']}/expire", json={}, headers=h
    )
    assert r2.status_code == 409


@pytest.mark.anyio
async def test_cancel_only_for_drafts(client, master, roles, db):
    hr = make_user(db, "hr@oxp.com", [roles["HR_MANAGER"]])
    h = auth(hr.id)
    emp = (await create_employee(client, h, master)).json()
    c = await create_contract(client, h, master, emp["id"])

    r = await client.post(
        f"/api/v1/contracts/{c['id']}/cancel", json={}, headers=h
    )
    assert r.status_code == 200
    assert r.json()["status"] == "cancelled"

    # Cancel a cancelled contract -> 409; there is NO delete endpoint.
    r = await client.post(
        f"/api/v1/contracts/{c['id']}/cancel", json={}, headers=h
    )
    assert r.status_code == 409


@pytest.mark.anyio
async def test_activate_future_start_allowed_and_resolver(client, master, roles, db):
    """§2.5: pre-scheduled (future start) running contracts are allowed, but
    the payroll resolver must NOT treat them as applicable before start."""
    from app.modules.employees.service_contract import get_applicable_contract

    hr = make_user(db, "hr@oxp.com", [roles["HR_MANAGER"]])
    h = auth(hr.id)
    emp = (await create_employee(client, h, master)).json()
    c = await create_contract(client, h, master, emp["id"], start="2099-01-15")
    r = await activate(client, h, c["id"])
    assert r.status_code == 200
    assert r.json()["status"] == "running"

    # Not applicable to today (2099 > 2026).
    assert get_applicable_contract(db, emp["id"], date.today()) is None
    # Applicable once the start date arrives.
    applicable = get_applicable_contract(db, emp["id"], date(2099, 1, 15))
    assert applicable is not None and applicable.id == c["id"]


@pytest.mark.anyio
async def test_contract_create_for_inactive_employee_rejected(
    client, master, roles, db
):
    hr = make_user(db, "hr@oxp.com", [roles["HR_MANAGER"]])
    h = auth(hr.id)
    emp = (await create_employee(client, h, master)).json()
    await client.delete(f"/api/v1/employees/{emp['id']}", headers=h)  # inactive

    r = await client.post(
        "/api/v1/contracts",
        json={
            "employee_id": emp["id"],
            "department_id": master.dept.id,
            "job_position_id": master.pos.id,
            "working_schedule_id": master.sched.id,
            "salary_structure_id": master.structure.id,
            "wage_monthly": "50000.00",
            "start_date": "2024-01-15",
        },
        headers=h,
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Working schedule edge cases (§2.3)
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_zero_line_schedule_exists_but_not_assignable(
    client, master, roles, db
):
    """A zero-line schedule may exist (draft-ish, total_weekly_hours=0) but
    cannot be assigned to a new employee (422 with a clear message)."""
    hr = make_user(db, "hr@oxp.com", [roles["HR_MANAGER"]])
    h = auth(hr.id)

    r = await client.post(
        "/api/v1/working-schedules",
        json={"name": "Empty Sched", "schedule_type": "custom", "lines": []},
        headers=h,
    )
    assert r.status_code == 201
    empty_id = r.json()["id"]

    # List view reports the derived 0.00 hours.
    r = await client.get("/api/v1/working-schedules", headers=h)
    item = next(i for i in r.json()["items"] if i["id"] == empty_id)
    assert item["total_weekly_hours"] == "0.00"

    r = await create_employee(
        client, h, master, working_schedule_id=empty_id
    )
    assert r.status_code == 422
    assert "at least one working day" in r.json()["detail"]


@pytest.mark.anyio
async def test_schedule_lines_validation(client, roles, db):
    hr = make_user(db, "hr@oxp.com", [roles["HR_MANAGER"]])
    h = auth(hr.id)

    # end_time <= start_time -> 422 (friendly, DB CHECK backs it up)
    r = await client.post(
        "/api/v1/working-schedules",
        json={
            "name": "Inverted", "schedule_type": "full_time",
            "lines": [{"day_of_week": 0, "start_time": "18:00",
                       "end_time": "09:00", "break_minutes": 0}],
        },
        headers=h,
    )
    assert r.status_code == 422

    # day_of_week outside 0-6 -> 422
    r = await client.post(
        "/api/v1/working-schedules",
        json={
            "name": "Bad Day", "schedule_type": "full_time",
            "lines": [{"day_of_week": 7, "start_time": "09:00",
                       "end_time": "18:00", "break_minutes": 0}],
        },
        headers=h,
    )
    assert r.status_code == 422


@pytest.mark.anyio
async def test_replace_lines_in_one_transaction(client, master, roles, db):
    """PUT /working-schedules/{id}/lines replaces the full set atomically."""
    hr = make_user(db, "hr@oxp.com", [roles["HR_MANAGER"]])
    h = auth(hr.id)
    sched = (
        await client.post(
            "/api/v1/working-schedules",
            json={
                "name": "Swap", "schedule_type": "full_time",
                "lines": [{"day_of_week": 0, "start_time": "09:00",
                           "end_time": "13:00", "break_minutes": 0}],
            },
            headers=h,
        )
    ).json()

    r = await client.put(
        f"/api/v1/working-schedules/{sched['id']}/lines",
        json=[{"day_of_week": 1, "start_time": "10:00",
               "end_time": "14:00", "break_minutes": 30}],
        headers=h,
    )
    assert r.status_code == 200
    assert len(r.json()["lines"]) == 1
    assert r.json()["lines"][0]["day_of_week"] == 1
    assert r.json()["total_weekly_hours"] == "3.50"  # 4h - 30min

    # A bad payload leaves the schedule untouched (validate before mutate).
    r = await client.put(
        f"/api/v1/working-schedules/{sched['id']}/lines",
        json=[{"day_of_week": 0, "start_time": "09:00",
               "end_time": "09:00", "break_minutes": 0}],
        headers=h,
    )
    assert r.status_code == 422
    r = await client.get(
        f"/api/v1/working-schedules/{sched['id']}", headers=h
    )
    assert len(r.json()["lines"]) == 1


@pytest.mark.anyio
async def test_soft_delete_schedule_in_use_conflict(client, master, roles, db):
    hr = make_user(db, "hr@oxp.com", [roles["HR_MANAGER"]])
    h = auth(hr.id)
    await create_employee(client, h, master)  # uses master.sched

    r = await client.delete(
        f"/api/v1/working-schedules/{master.sched.id}", headers=h
    )
    assert r.status_code == 409
    assert "1 active employee" in r.json()["detail"]


# ---------------------------------------------------------------------------
# Employee edge cases (§2.4)
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_employee_cannot_read_other_employee(client, master, roles, db):
    hr = make_user(db, "hr@oxp.com", [roles["HR_MANAGER"]])
    h = auth(hr.id)
    a = (await create_employee(client, h, master, work_email="a@oxp.com")).json()
    b = (await create_employee(client, h, master, work_email="b@oxp.com")).json()

    emp_user = make_user(
        db, "emp@oxp.com", [roles["EMPLOYEE"]], employee_id=a["id"]
    )
    eh = auth(emp_user.id)

    assert (await client.get(f"/api/v1/employees/{b['id']}", headers=eh)).status_code == 403
    assert (await client.get(f"/api/v1/employees/{a['id']}", headers=eh)).status_code == 200
    assert (await client.get(f"/api/v1/employees/{b['id']}/contracts", headers=eh)).status_code == 403
    assert (await client.get(f"/api/v1/employees/{b['id']}/related-summary", headers=eh)).status_code == 403

    # EMPLOYEE cannot write.
    r = await client.post(
        "/api/v1/employees",
        json={
            "full_name": "X", "work_email": "x@oxp.com",
            "department_id": master.dept.id, "job_position_id": master.pos.id,
            "working_schedule_id": master.sched.id,
            "employee_type": "full_time", "date_of_joining": "2024-01-15",
        },
        headers=eh,
    )
    assert r.status_code == 403


@pytest.mark.anyio
async def test_manager_inactive_warning(client, master, roles, db):
    """Assigning an inactive manager is ALLOWED but surfaced as a warning."""
    hr = make_user(db, "hr@oxp.com", [roles["HR_MANAGER"]])
    h = auth(hr.id)
    manager = (
        await create_employee(client, h, master, work_email="mgr@oxp.com")
    ).json()
    await client.delete(f"/api/v1/employees/{manager['id']}", headers=h)

    r = await create_employee(
        client, h, master, full_name="Reports", work_email="rep@oxp.com",
        manager_id=manager["id"],
    )
    assert r.status_code == 201
    assert "manager is inactive" in r.json()["warnings"]
    assert r.json()["manager"]["id"] == manager["id"]


@pytest.mark.anyio
async def test_management_cycle_rejected(client, master, roles, db):
    hr = make_user(db, "hr@oxp.com", [roles["HR_MANAGER"]])
    h = auth(hr.id)
    a = (await create_employee(client, h, master, work_email="a@oxp.com")).json()
    b = (await create_employee(client, h, master, work_email="b@oxp.com")).json()

    r = await client.patch(
        f"/api/v1/employees/{b['id']}",
        json={"manager_id": a["id"]}, headers=h,
    )
    assert r.status_code == 200

    r = await client.patch(
        f"/api/v1/employees/{a['id']}",
        json={"manager_id": b["id"]}, headers=h,
    )
    assert r.status_code == 422
    assert "cycle" in r.json()["detail"]

    # Self-management -> 422.
    r = await client.patch(
        f"/api/v1/employees/{a['id']}",
        json={"manager_id": a["id"]}, headers=h,
    )
    assert r.status_code == 422


@pytest.mark.anyio
async def test_employee_soft_delete_and_terminate(client, master, roles, db):
    hr = make_user(db, "hr@oxp.com", [roles["HR_MANAGER"]])
    h = auth(hr.id)
    emp = (await create_employee(client, h, master)).json()

    r = await client.delete(f"/api/v1/employees/{emp['id']}", headers=h)
    assert r.status_code == 204
    assert (await client.get(f"/api/v1/employees/{emp['id']}", headers=h)).json()["status"] == "inactive"

    emp2 = (await create_employee(client, h, master, work_email="t@oxp.com")).json()
    r = await client.post(
        f"/api/v1/employees/{emp2['id']}/terminate", headers=h
    )
    assert r.status_code == 200
    assert r.json()["status"] == "terminated"

    # Terminating twice -> 409.
    r = await client.post(
        f"/api/v1/employees/{emp2['id']}/terminate", headers=h
    )
    assert r.status_code == 409


@pytest.mark.anyio
async def test_employee_update_touches_only_employee_row(
    client, master, roles, db
):
    """§2.4: PATCHing department_id must NOT retroactively change past
    contracts (contracts snapshot their own department at creation)."""
    hr = make_user(db, "hr@oxp.com", [roles["HR_MANAGER"]])
    h = auth(hr.id)
    emp = (await create_employee(client, h, master)).json()
    c = await create_contract(client, h, master, emp["id"])

    other_dept = (
        await client.post(
            "/api/v1/departments",
            json={"name": "Finance", "company_id": master.company.id}, headers=h,
        )
    ).json()
    other_pos = (
        await client.post(
            "/api/v1/job-positions",
            json={"title": "Accountant", "department_id": other_dept["id"]}, headers=h,
        )
    ).json()

    r = await client.patch(
        f"/api/v1/employees/{emp['id']}",
        json={"department_id": other_dept["id"],
              "job_position_id": other_pos["id"]},
        headers=h,
    )
    assert r.status_code == 200
    assert r.json()["department"]["id"] == other_dept["id"]

    # The contract still snapshots the ORIGINAL department.
    g = await client.get(f"/api/v1/contracts/{c['id']}", headers=h)
    assert g.json()["department"]["id"] == master.dept.id


@pytest.mark.anyio
async def test_employee_list_filters_and_kanban(client, master, roles, db):
    hr = make_user(db, "hr@oxp.com", [roles["HR_MANAGER"]])
    h = auth(hr.id)
    await create_employee(client, h, master, work_email="one@oxp.com")
    await create_employee(
        client, h, master, full_name="Inactive Person",
        work_email="two@oxp.com", status="inactive",
    )

    r = await client.get("/api/v1/employees?search=Inactive", headers=h)
    assert r.status_code == 200
    assert r.json()["total"] == 1

    r = await client.get("/api/v1/employees?status=inactive", headers=h)
    assert r.json()["total"] == 1

    r = await client.get(
        "/api/v1/employees?group_by=status", headers=h
    )
    assert r.status_code == 200
    keys = {g["key"]: g["count"] for g in r.json()["groups"]}
    assert keys.get("active") == 1
    assert keys.get("inactive") == 1


@pytest.mark.anyio
async def test_related_summary_counts(client, master, roles, db):
    hr = make_user(db, "hr@oxp.com", [roles["HR_MANAGER"]])
    h = auth(hr.id)
    emp = (await create_employee(client, h, master)).json()
    await create_contract(client, h, master, emp["id"])

    # Directly insert one attendance + one time-off request/allocation.
    from datetime import datetime, timedelta, timezone

    tot = TimeOffType(
        name="PTO", unit=TimeOffUnit.days, requires_allocation=True,
        requires_approval=True, affects_payroll=True,
        company_id=master.company.id, is_active=True,
    )
    db.add(tot)
    db.flush()
    now = datetime.now(timezone.utc)
    db.add(Attendance(
        employee_id=emp["id"], check_in=now - timedelta(hours=8),
        check_out=now, worked_hours=Decimal("8.00"),
        status=AttendanceStatus.present,
    ))
    db.add(TimeOffRequest(
        employee_id=emp["id"], time_off_type_id=tot.id,
        date_from=date(2026, 1, 5), date_to=date(2026, 1, 6),
        duration=Decimal("2.00"), status=TimeOffRequestStatus.approved,
    ))
    db.add(TimeOffAllocation(
        employee_id=emp["id"], time_off_type_id=tot.id,
        allocated_amount=Decimal("10.00"),
        valid_from=date(2026, 1, 1), valid_to=None,
        status=AllocationStatus.approved,
    ))
    db.commit()

    r = await client.get(
        f"/api/v1/employees/{emp['id']}/related-summary", headers=h
    )
    assert r.status_code == 200
    assert r.json() == {
        "contracts_count": 1, "attendance_count": 1,
        "time_off_count": 1, "allocations_count": 1,
    }


# ---------------------------------------------------------------------------
# Misc §2 edge cases
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_duplicate_department_name_conflict(client, master, roles, db):
    hr = make_user(db, "hr@oxp.com", [roles["HR_MANAGER"]])
    h = auth(hr.id)
    r1 = await client.post(
        "/api/v1/departments",
        json={"name": "Ops", "company_id": master.company.id}, headers=h,
    )
    assert r1.status_code == 201
    r2 = await client.post(
        "/api/v1/departments",
        json={"name": "Ops", "company_id": master.company.id}, headers=h,
    )
    assert r2.status_code == 409
    assert "already exists" in r2.json()["detail"]


@pytest.mark.anyio
async def test_inactive_schedule_not_assignable(client, master, roles, db):
    hr = make_user(db, "hr@oxp.com", [roles["HR_MANAGER"]])
    h = auth(hr.id)
    inactive_sched = (
        await client.post(
            "/api/v1/working-schedules",
            json={
                "name": "Old", "schedule_type": "full_time",
                "lines": [{"day_of_week": 0, "start_time": "09:00",
                           "end_time": "18:00", "break_minutes": 60}],
            },
            headers=h,
        )
    ).json()
    r = await client.patch(
        f"/api/v1/working-schedules/{inactive_sched['id']}",
        json={"is_active": False}, headers=h,
    )
    assert r.status_code == 200

    r = await create_employee(
        client, h, master, working_schedule_id=inactive_sched["id"]
    )
    assert r.status_code == 422
    assert "inactive" in r.json()["detail"]


@pytest.mark.anyio
async def test_department_parent_must_exist_and_be_active(
    client, master, roles, db
):
    hr = make_user(db, "hr@oxp.com", [roles["HR_MANAGER"]])
    h = auth(hr.id)

    # Missing parent -> 404.
    r = await client.post(
        "/api/v1/departments",
        json={"name": "Orphan", "parent_department_id": 99999}, headers=h,
    )
    assert r.status_code == 404

    # Inactive parent -> 422.
    inactive = (
        await client.post(
            "/api/v1/departments",
            json={"name": "Dead", "company_id": master.company.id}, headers=h,
        )
    ).json()
    await client.delete(f"/api/v1/departments/{inactive['id']}", headers=h)
    r = await client.post(
        "/api/v1/departments",
        json={"name": "Child", "parent_department_id": inactive["id"]}, headers=h,
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Pure-function unit tests (no DB)
# ---------------------------------------------------------------------------

class _FakeLine:
    def __init__(self, sh, sm, eh, em, break_minutes):
        self.start_time = time(sh, sm)
        self.end_time = time(eh, em)
        self.break_minutes = break_minutes


def test_compute_total_weekly_hours_pure():
    # 5 x (09:00-18:00, 60min break) = 5 x 8h = 40h
    lines = [_FakeLine(9, 0, 18, 0, 60) for _ in range(5)]
    assert compute_total_weekly_hours(lines) == Decimal("40.00")

    # Half-open boundaries and zero break.
    lines = [_FakeLine(9, 0, 13, 0, 0), _FakeLine(13, 0, 17, 0, 0)]
    assert compute_total_weekly_hours(lines) == Decimal("8.00")

    # Empty schedule -> 0.
    assert compute_total_weekly_hours([]) == Decimal("0.00")

    # Minutes round to 2dp hours.
    lines = [_FakeLine(9, 0, 9, 30, 0)]
    assert compute_total_weekly_hours(lines) == Decimal("0.50")