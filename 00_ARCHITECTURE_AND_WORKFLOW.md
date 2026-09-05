# PeoplePay360 — HR & Payroll Platform
## Master Build Plan for Claude Code (4-person team, 24-hour hackathon)

Stack: **FastAPI + PostgreSQL + SQLAlchemy 2.0 + Alembic + Pydantic v2**

This document is the shared contract. Read it fully before opening your own file:
- `01_DB_SCHEMA_ELDO.md`
- `02_BACKEND_AMEEN_EMPLOYEE_CONTRACTS.md`
- `03_BACKEND_AMBUJ_ATTENDANCE_TIMEOFF.md`
- `04_BACKEND_STEVE_PAYROLL_ANALYTICS.md`

Each of those files is a **self-contained Claude Code prompt** — paste the whole file into
Claude Code in that person's own working branch/folder and let it build that vertical slice.

---

## 1. Why this split has zero merge conflicts

The system is split into **4 vertical domains**. Each person owns a disjoint set of
folders/files. Nobody edits another person's files. The only shared files
(`main.py`, `core/*`, `models/*`) are written **once, early, by Eldo**, then frozen —
everyone else only *imports* from them, never edits them.

| Person | Role | Owns |
|---|---|---|
| **Eldo** | DB | `core/`, `models/` (ALL tables), `alembic/` migrations, seed data, ERD |
| **Ameen** | Backend | Employee, Department, Job Position, Working Schedule, Contracts |
| **Ambuj** | Backend + Frontend | Attendance, Time Off (Types, Allocations, Requests) |
| **Steve** | Backend + Analytics | Salary Structures/Rules, Payrun, Payslip, PDF/email, Payroll Dashboard |

Auth/RBAC (login, users, roles) is small and foundational — it is bundled into **Eldo's**
scope (`core/security.py`, `modules/auth/`) since it must exist before anyone else can
build role-gated endpoints, and it touches the same `models/auth.py` file he already owns.

### The golden rule
> **If you need a field/table that doesn't exist yet, do NOT edit `models/`.**
> Post in the team chat: "Eldo, please add column X to table Y." Eldo pushes a 2-minute
> migration + model update. This keeps `models/` single-owner and merge-conflict-free.

---

## 2. Repository layout (create this exact skeleton first)

```
peoplepay360/
├── alembic/
│   ├── versions/
│   └── env.py
├── alembic.ini
├── app/
│   ├── main.py                       # Eldo — written once, frozen after Hour 2
│   ├── core/
│   │   ├── config.py                 # Eldo — env vars, settings (pydantic-settings)
│   │   ├── database.py               # Eldo — engine, SessionLocal, get_db()
│   │   ├── security.py               # Eldo — password hashing, JWT encode/decode
│   │   ├── dependencies.py           # Eldo — get_current_user, require_roles()
│   │   ├── base.py                   # Eldo — Declarative Base, TimestampMixin, UUID default
│   │   └── exceptions.py             # Eldo — AppException classes + FastAPI handlers
│   ├── models/                       # Eldo owns ALL of this directory
│   │   ├── __init__.py               # imports every model so Alembic autogenerate sees them
│   │   ├── enums.py
│   │   ├── auth.py                   # User, Role, Permission, UserRole, RolePermission
│   │   ├── organization.py           # Company, Department, JobPosition, WorkingSchedule, WorkingScheduleLine
│   │   ├── employee.py               # Employee, Contract, EmployeeBankDetail
│   │   ├── attendance.py             # Attendance
│   │   ├── timeoff.py                # TimeOffType, TimeOffAllocation, TimeOffRequest
│   │   └── payroll.py                # SalaryStructure, SalaryRule, SalaryStructureRuleLine,
│   │                                   Payrun, PayrunEmployee, Payslip, PayslipLine, PayslipWarning
│   ├── schemas/                      # Pydantic v2 request/response models — split by owner
│   │   ├── auth.py                   # Eldo
│   │   ├── employee.py               # Ameen  (also organization.py if you prefer separate)
│   │   ├── attendance_timeoff.py     # Ambuj
│   │   └── payroll.py                # Steve
│   ├── modules/
│   │   ├── auth/
│   │   │   ├── __init__.py           # Eldo
│   │   │   ├── router.py
│   │   │   └── service.py
│   │   ├── employees/                # Ameen — everything below is Ameen's alone
│   │   │   ├── __init__.py
│   │   │   ├── router.py             # can split into router_employee.py, router_contract.py, router_schedule.py
│   │   │   └── service.py
│   │   ├── attendance_timeoff/       # Ambuj — everything below is Ambuj's alone
│   │   │   ├── __init__.py
│   │   │   ├── router.py
│   │   │   └── service.py
│   │   └── payroll/                  # Steve — everything below is Steve's alone
│   │       ├── __init__.py
│   │       ├── router.py             # salary structures/rules, payrun, payslip
│   │       ├── dashboard_router.py   # analytics/dashboard endpoints
│   │       ├── service.py
│   │       ├── engine.py             # salary rule computation engine
│   │       └── pdf.py                # payslip PDF rendering + email
│   └── seed/
│       └── seed_data.py              # Eldo — demo data generator (all domains, run once)
├── tests/
│   ├── test_employees.py             # Ameen
│   ├── test_attendance_timeoff.py    # Ambuj
│   └── test_payroll.py               # Steve
├── requirements.txt                  # Eldo (everyone appends their own new deps in a PR, rare conflict — list is alphabetized)
├── .env.example
└── docker-compose.yml                # Eldo — postgres + app service
```

### How routers get wired without touching a shared file every time
`app/main.py` (written once by Eldo) does **static, one-time** imports:

```python
from fastapi import FastAPI
from app.core.exceptions import register_exception_handlers
from app.modules.auth.router import router as auth_router
from app.modules.employees.router import router as employees_router
from app.modules.attendance_timeoff.router import router as attendance_timeoff_router
from app.modules.payroll.router import router as payroll_router
from app.modules.payroll.dashboard_router import router as dashboard_router

app = FastAPI(title="PeoplePay360")
register_exception_handlers(app)

app.include_router(auth_router,               prefix="/api/v1/auth",        tags=["Auth"])
app.include_router(employees_router,          prefix="/api/v1",             tags=["Employees"])
app.include_router(attendance_timeoff_router, prefix="/api/v1",             tags=["Attendance & Time Off"])
app.include_router(payroll_router,            prefix="/api/v1/payroll",     tags=["Payroll"])
app.include_router(dashboard_router,          prefix="/api/v1/dashboard",   tags=["Dashboard"])
```

This file is created **before** anyone starts their module (Hour 0–1) with all 5 import
lines already present, even pointing at routers that don't exist yet (each person creates
an empty `router = APIRouter()` stub first thing so the app boots). **Nobody touches
`main.py` again after Hour 1.**

---

## 3. Git workflow

1. `main` branch is protected. Everyone branches off it:
   - `eldo/db-core`
   - `ameen/employees`
   - `ambuj/attendance-timeoff`
   - `steve/payroll-analytics`
2. Eldo merges `eldo/db-core` into `main` **first** (models, core, main.py skeleton,
   docker-compose, alembic init + first migration). Everyone else rebases onto `main`
   after that merge, *before* writing their own code.
3. Because each branch only ever touches its own folder(s) under `app/modules/<domain>/`,
   `app/schemas/<domain>.py`, and `tests/test_<domain>.py`, merges are fast-forward or
   trivial 3-way merges — **git will never see two people editing the same line.**
4. Commit early, commit often, push every 45–60 minutes so integration surprises are
   caught within the hour, not at hour 23.
5. If a schema change is needed mid-hackathon: Eldo makes the change on `eldo/db-core`,
   runs `alembic revision --autogenerate`, merges to `main` fast, and pings the affected
   person to `git pull` / rebase.

---

## 4. Shared conventions (all modules must follow these)

### 4.1 Response envelope
Every endpoint returns Pydantic response models directly (FastAPI's native behavior) —
**do not hand-roll a generic `{status, data}` wrapper**, it complicates OpenAPI docs and
frontend typing. Use HTTP status codes correctly instead (200/201/204/400/401/403/404/409/422).

### 4.2 Error handling
Use the shared `AppException` hierarchy from `core/exceptions.py`:
```python
class AppException(Exception): status_code = 400
class NotFoundException(AppException): status_code = 404
class ConflictException(AppException): status_code = 409
class ValidationException(AppException): status_code = 422
class ForbiddenException(AppException): status_code = 403
```
Raise these from services; a global handler converts them to
`{"detail": "<message>", "error_code": "<code>"}` JSON. Never raise raw `HTTPException`
inside service functions (keeps services testable without FastAPI import).

### 4.3 Layering (every module follows this)
`router.py` (HTTP concerns, auth dependency, status codes)
→ `service.py` (business logic, transactions, edge-case checks)
→ `models/*.py` (SQLAlchemy ORM, imported read-only)

Routers must be thin: parse request → call one service function → return.
All edge-case validation lives in the **service layer** so it's unit-testable without spinning up HTTP.

### 4.4 Pagination & filtering (apply to every list endpoint)
Standard query params: `?page=1&page_size=20&sort_by=<field>&sort_dir=asc|desc`
plus domain-specific filters (e.g. `?department_id=`, `?status=`). Response shape:
```json
{"items": [...], "total": 123, "page": 1, "page_size": 20}
```

### 4.5 Soft delete, not hard delete
Master/transactional records (Employee, Contract, Payslip, TimeOffRequest, etc.) are
never hard-deleted — use an `is_active` / status flag. Only pure lookup/config rows
created accidentally may be hard-deleted. This preserves history required by the
problem statement ("preserve finalized/paid payroll as historical records",
"retain contract history").

### 4.6 Auditing
Every table has `created_at`, `updated_at` (server-side defaults via `TimestampMixin`
in `core/base.py`). Status-changing actions (contract activation, request approval,
payrun validation) should also stamp `*_by_user_id` where relevant — see each domain
file for exact columns.

### 4.7 RBAC — roles from the problem statement
Five roles, enforced via `core/dependencies.py::require_roles(*roles)`:
- `EMPLOYEE` — read own employee/attendance/leave; create own attendance + time off requests
- `HR_MANAGER` — full CRUD on Employees/Attendance/Contracts/Working Schedules/Time Off; approve/refuse requests; **no payroll access**
- `HR_PAYROLL_USER` — all HR_MANAGER perms + create/read/update Payruns & Payslips; **read-only** Salary Structures/Rules
- `HR_PAYROLL_MANAGER` — all HR_PAYROLL_USER perms + full CRUD on Payruns, Payslips, Salary Structures, Salary Rules
- `ADMIN` — full access to everything, user/role management

A user can hold multiple roles (many-to-many `user_roles`). Permission checks in
`require_roles()` should be an OR across the user's roles.

**Critical edge case (from the wireframe notes):** *users must not be able to
assign or elevate their own roles.* Enforce in the Admin user-management endpoint:
reject any request where `current_user.id == target_user.id` AND the payload changes
`roles`.

### 4.8 Timezones & dates
Store all timestamps as `timestamptz` (UTC) in Postgres. Store pure dates
(`period_start`, `date_of_joining`, leave `date_from`) as `date`, not `timestamp`.
Never do date-math in Python with naive datetimes — use `date`/`datetime` with tzinfo
consistently to avoid off-by-one-day bugs across attendance/payroll periods.

### 4.9 Money
All monetary columns are `NUMERIC(12,2)` — never `FLOAT`/`DOUBLE` (float rounding
errors are unacceptable in payroll). Python-side use `Decimal`, never `float`, for any
salary computation.

### 4.10 IDs
Primary keys are `BIGSERIAL` (or `UUID` — pick one and note it in `01_DB_SCHEMA_ELDO.md`;
recommendation: `BIGSERIAL` for simplicity/perf in a 24h hackathon, `UUID` only if the
frontend needs non-guessable IDs). Foreign keys always `ON DELETE RESTRICT` by default
except pure config lookups noted per-table in the schema file.

---

## 5. Cross-cutting edge cases every module must respect

These recur across domains and are easy to miss — call them out explicitly in code
comments/tests wherever relevant:

1. **Concurrent writes** — two users editing the same Contract/Payslip/Request at once.
   Use SQLAlchemy optimistic locking (`version_id_col`) on high-contention tables:
   Contract, Payslip, TimeOffAllocation, Payrun.
2. **Soft-deleted / inactive parents** — creating an Attendance/Contract/Payslip for an
   `inactive`/terminated Employee should be blocked (or explicitly allowed only for
   backfilling historical data by Admin — decide and document per endpoint).
3. **Referential existence** — every FK in a request body (department_id, employee_id,
   salary_structure_id, etc.) must be validated to (a) exist and (b) be active, with a
   clear 404/409, not a raw DB `IntegrityError` leaking to the client.
4. **Empty / partial data** — an Employee with no Contract yet cannot be included in a
   Payrun (must surface as a payslip warning, not a 500 error). An Employee with no
   Working Schedule falls back to a documented default schedule.
5. **Pagination edge cases** — `page=0`, negative `page_size`, `page_size` beyond a max
   cap (e.g., 200) must be clamped/validated, not crash.
6. **Timezone/date boundaries** — attendance check-in at 23:58 and check-out at 00:15
   next day (overnight shift); payroll period boundaries (`period_start <= x <= period_end`,
   inclusive both ends, defined once as a shared query helper).
7. **Idempotency of bulk actions** — "Send Payslips" bulk email, "Compute" on a Payrun,
   must be safe to click twice (don't double-send emails, don't duplicate payslip lines —
   recompute should replace, not append).
8. **Authorization boundary leaks** — an `EMPLOYEE` role user hitting
   `/employees/{other_id}` or `/payslips/{other_id}` must get 403/404, never another
   person's payroll data.

---

## 6. Suggested 24-hour timeline

| Hours | Eldo (DB) | Ameen | Ambuj | Steve |
|---|---|---|---|---|
| 0–2 | Repo skeleton, docker-compose, `core/`, all `models/`, first Alembic migration, seed script skeleton, `main.py` with stub routers | Read schema, stub router/service files | Read schema, stub router/service files | Read schema, stub router/service files |
| 2–8 | Auth/RBAC endpoints (login, JWT, user CRUD, role assignment), finalize seed data | Employee + Department + Job Position CRUD + Working Schedule CRUD | Attendance CRUD + Time Off Types CRUD | Salary Structure + Salary Rule CRUD + computation engine skeleton |
| 8–14 | Support requests for schema tweaks; write DB-level tests/indexes; add audit log table if time permits | Contract CRUD + running-contract exclusivity logic + smart-button "related records" endpoints | Time Off Allocations + Requests + balance computation + approval workflow | Payrun 2-step wizard + Payslip computation engine wired to Salary Rules |
| 14–19 | Backfill data quality checks, index tuning (`EXPLAIN ANALYZE` on dashboard queries) | Polish edge cases, write tests | Polish edge cases, write tests, start frontend screens | Payslip PDF generation + bulk email + payrun warnings |
| 19–22 | Integration pass across all modules together | Integration pass | Integration pass + frontend wiring | Payroll Dashboard aggregation endpoints + charts data |
| 22–24 | Final demo data reset, smoke test full flow | Demo script | Demo script / frontend polish | Demo script |

---

## 7. Demo flow to keep in mind while building
Two end-to-end scenarios must work flawlessly for the 5-minute walkthrough:
1. **Employee → Payslip**: create/open employee → view running contract → attendance →
   create Payrun (2-step wizard) → Compute → see warnings → Validate → Mark Paid →
   print Payslip PDF → Send Payslips email.
2. **Leave allocation → request**: create Time Off Type → allocate balance to employee →
   approve allocation → employee raises Time Off Request → approve request → balance
   decreases → dashboard reflects updated Time Off overview.

Build towards these two flows being demo-able early, then harden edge cases.
