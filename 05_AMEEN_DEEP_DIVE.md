# PeoplePay360 — The Complete Deep Dive

> **For Ameen.** Everything about the system, from the tech stack down to the
> fiddliest business rule: **what** it is, **why** it was built that way,
> **where** the logic actually lives in the code, and **why not** the obvious
> alternative. This is the document I wish existed on day one — it reads the
> actual code, not the plan.
>
> Companion docs: `00_ARCHITECTURE_AND_WORKFLOW.md` (the shared contract),
> `README.md` (Eldo's slice + demo data), `HRMS OXP - 24 hours.excalidraw`
> (the wireframe).

---

## Table of contents

1. [TL;DR — the system in 10 bullets](#1-tldr)
2. [The product & the problem](#2-the-product--the-problem)
3. [Tech stack — every layer, with WHY and WHY-NOT](#3-tech-stack)
   - 3.1 One-line stack
   - 3.2 Python 3.12
   - 3.3 FastAPI + Uvicorn
   - 3.4 Pydantic v2
   - 3.5 SQLAlchemy 2.0
   - 3.6 Alembic
   - 3.7 PostgreSQL 16
   - 3.8 bcrypt (not passlib)
   - 3.9 PyJWT
   - 3.10 pytest + httpx + anyio
   - 3.11 reportlab
   - 3.12 Docker Compose
   - 3.13 Frontend: React 19 + Vite + TypeScript
   - 3.14 What's deliberately NOT in the stack
4. [Architecture — how the pieces fit](#4-architecture)
   - 4.1 Monolith with vertical slices (the 4-person split)
   - 4.2 The golden rule: models are frozen and single-owner
   - 4.3 Layering: router → service → model
   - 4.4 Response envelope & status codes
   - 4.5 Error handling: the AppException hierarchy
   - 4.6 Pagination & filtering contract
   - 4.7 Soft delete & auditing
   - 4.8 RBAC model (roles vs permissions)
5. [Data model deep dive (25 tables + 2 views)](#5-data-model-deep-dive)
   - 5.1 ERD at a glance
   - 5.2 Conventions: naming, PKs, timestamps, money, dates, FKs
   - 5.3 Enums (PG-native)
   - 5.4 Intentional denormalization — what IS stored and why
   - 5.5 Deliberately NOT stored — the SQL views and why
   - 5.6 The one-running-contract partial unique index
   - 5.7 Optimistic locking (version_id)
   - 5.8 Indexing strategy
6. [Business logic — Auth & RBAC (Eldo's)](#6-business-logic-auth--rbac)
7. [Business logic — Employee module (YOURS, Ameen)](#7-business-logic-employee-module-yours)
   - 7.1 Departments
   - 7.2 Job Positions
   - 7.3 Working Schedules
   - 7.4 Employees
   - 7.5 Contracts
   - 7.6 Serialization & the N+1 problem
   - 7.7 Files you own and why they're split that way
8. [Business logic — Attendance & Time Off (Ambuj's)](#8-business-logic-attendance--time-off)
9. [Business logic — Payroll (Steve's)](#9-business-logic-payroll)
   - 9.1 The salary rule engine (no bare eval)
   - 9.2 The payrun lifecycle
   - 9.3 The contract resolver — how Steve depends on YOUR data
10. [Cross-cutting edge cases — the checklist](#10-cross-cutting-edge-cases)
11. [Testing strategy](#11-testing-strategy)
12. [Frontend deep dive](#12-frontend-deep-dive)
13. [Seed data & demo flows](#13-seed-data--demo-flows)
14. [Known gaps & future hardening](#14-known-gaps--future-hardening)
15. [Cheat sheet — every decision in one table](#15-cheat-sheet)
16. [Glossary](#16-glossary)

---

## 1. TL;DR

- **What:** PeoplePay360 is an HR & Payroll platform built by 4 people in a
  24-hour hackathon: Companies/Departments/Job Positions/Working Schedules,
  Employees + Contracts, Attendance + Time Off, Salary Structures + Payruns +
  Payslips (with PDF + email), RBAC with 5 roles, and a React frontend.
- **Stack:** Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2.0 (sync,
  psycopg2), Alembic, PostgreSQL 16, bcrypt, PyJWT, pytest+httpx, reportlab,
  Docker Compose. Frontend: React 19 + Vite + TypeScript.
- **Shape:** A **monolith with 4 vertical slices**, one owner per slice, so
  four people can work in parallel with **zero merge conflicts**. The shared
  files (`app/main.py`, `app/core/*`, `app/models/*`) were written once by
  Eldo and are **frozen**.
- **Golden rule:** nobody edits `app/models/` except Eldo. If you need a
  column, you *ask* Eldo for a migration. That's why every service layer
  imports models read-only.
- **Layering:** `router.py` (thin HTTP) → `service.py` (all business logic,
  transactions, edge cases) → models. Services never raise raw `HTTPException`
  — they raise `AppException` subclasses mapped by a global handler.
- **Two headline business rules:** (1) one `running` contract per employee,
  enforced by a **partial unique index** in Postgres *and* transaction logic
  in the service; (2) money and leave balances are **never stored as running
  totals** — they're derived (SQL views), so they can't drift out of sync.
- **Soft delete everywhere.** Employees, contracts, departments, payruns,
  payslips, time-off types: nothing is ever hard-deleted. History is the
  product.
- **Payroll correctness guards:** `NUMERIC(12,2)` money (never float),
  `Decimal` + `ROUND_HALF_UP` everywhere, payslip lines *snapshot* the salary
  rule at compute time, a restricted AST walker (never `eval()`) evaluates
  stored formulas, and every per-rule failure becomes a **payslip warning**
  instead of a 500.
- **Tests** run against a real Postgres (not SQLite) so the partial unique
  index and CHECK constraints actually fire; the httpx `AsyncClient` hits the
  real app with `get_db` overridden.

---

## 2. The product & the problem

### 2.0 What the app does, end to end

```
HR master data (departments, positions, schedules, employees, contracts)
        │
        ▼
Attendance (check-in/out, late/overtime/missing-checkout, corrections)
Time Off (types, allocations, requests, approvals, live balances)
        │
        ▼
Payroll (salary rules/structures → payrun wizard → compute → validate → paid)
        │
        ▼
Payslips (line-by-line breakdown, warnings, PDF, bulk email)
```

Two demo flows must work flawlessly for the 5-minute walkthrough:

1. **Employee → Payslip:** create/open employee → view running contract →
   attendance → create Payrun (2-step wizard) → Compute → see warnings →
   Validate → Mark Paid → print Payslip PDF → Send Payslips email.
2. **Leave allocation → request:** create Time Off Type → allocate balance →
   approve allocation → employee raises Time Off Request → approve → balance
   decreases → dashboard reflects it.

Everything in the codebase exists to serve those two flows *and* survive the
edge cases a demo judge will poke at: concurrent edits, out-of-order contract
activation, negative salaries, missing contracts, employees with no bank
details, overnight shifts, page=0 pagination, and role boundary leaks.

---

## 3. Tech stack

### 3.1 The one-line stack

> **Python 3.12 · FastAPI ≥0.115 · Uvicorn · Pydantic v2 + pydantic-settings ·
> SQLAlchemy 2.0 (sync) + psycopg2 · Alembic ≥1.13 · PostgreSQL 16 ·
> bcrypt ≥4.0 · PyJWT ≥2.9 · pytest + httpx + anyio · reportlab ≥4.0 ·
> Docker Compose.** Frontend: **React 19 · Vite · TypeScript · react-router 7 ·
> oxlint.**

Locked in `requirements.txt` (alphabetized, a rare shared file — appending is
a PR, not a silent edit).

### 3.2 Python 3.12

- **WHAT:** `python:3.12-slim` in the Dockerfile.
- **WHY:** modern, fast, great typing support (`X | None` syntax used
  everywhere), and every library on the list supports it.
- **WHY NOT 3.11/3.13:** 3.12 is the current sweet spot — 3.13 was too new at
  hackathon time for all wheels (e.g. some binary deps) to be safe.

### 3.3 FastAPI + Uvicorn

- **WHAT:** FastAPI is the web framework; Uvicorn is the ASGI server
  (`uvicorn app.main:app --host 0.0.0.0 --port 8000`).
- **WHY FastAPI:**
  - **Pydantic validation for free** — request bodies, query params, and
    responses are declared as typed models; invalid input returns a clean 422
    with field errors (`_validation_error_handler` shapes it).
  - **Auto OpenAPI `/docs`** — the definition of done says "documented in
    FastAPI's /docs", and FastAPI gives it for free from type hints and
    `summary=`/`description=` docstrings.
  - **Dependency injection** — `Depends(get_db)`, `Depends(require_roles(...))`
    compose cleanly and are the backbone of the RBAC design.
  - **Async-capable** but lets you write sync endpoints (run in a threadpool)
    — perfect for the sync SQLAlchemy stack (see 3.5).
- **WHY NOT:**
  - **Django:** batteries (admin, ORM, auth, migrations) but heavy, opinionated,
    and its ORM+admin would fight the "we own the schema, hand-tuned" approach;
    also 4 people learning Django's magic in 24h is riskier than FastAPI's
    explicit style.
  - **Flask:** minimal, but you hand-roll validation, OpenAPI, and DI — that's
    hours of yak-shaving for zero payoff here.
  - **Node/Express or Go:** nothing wrong with them, but the team's core was
    Python and the domain (payroll decimal math, SQL) plays to Python.

### 3.4 Pydantic v2

- **WHAT:** All request/response schemas are Pydantic v2 models
  (`app/schemas/*`), and `pydantic-settings` loads env config in
  `app/core/config.py`.
- **WHY:** validation at the boundary (e.g. `condecimal(ge=0, max_digits=12,
  decimal_places=2)` for `wage_monthly`, `EmailStr` for emails, `Field(ge=0,
  le=6)` for `day_of_week`), plus `from_attributes=True` serialization
  straight off ORM objects for the simple entities.
- **Key convention from the architecture doc §3:** **separate `*Create`,
  `*Update`, `*Read` schemas per entity — never reuse one schema for both
  directions.** Update schemas are all-optional and the service uses
  `model_dump(exclude_unset=True)` so "absent" (leave unchanged) is
  distinguishable from "explicitly null" (clear the field) — that's how PATCH
  can clear `manager_id` or `end_date`.
- **WHY NOT:** hand-rolled `attrs`/dataclasses — you'd lose boundary
  validation and OpenAPI generation.

### 3.5 SQLAlchemy 2.0 (sync) + psycopg2

- **WHAT:** SQLAlchemy 2.0 typed ORM (`Mapped[...]` / `mapped_column`),
  **synchronous** engine, `psycopg2-binary` driver. One engine, one
  `SessionLocal` (`app/core/database.py`), shared by Alembic, seeds, and every
  module service. `pool_pre_ping=True` to survive DB restarts.
- **WHY sync (this is a real decision, not laziness):**
  - **One code path for everything.** Alembic migrations and the seed script
    are sync; async would mean a second engine/session stack for them.
  - **Simpler debugging.** No `await` everywhere, no event-loop footguns
    during a 24h crunch.
  - **FastAPI runs sync endpoints in a threadpool**, so you lose almost
    nothing at this scale (a hackathon demo is not a 10k-RPS service).
  - The docs themselves say: *"Uses synchronous psycopg2 (simplest for a 24h
    hackathon — Alembic, seeds, and all module services share one engine)."*
- **WHY NOT asyncpg / async SQLAlchemy:** real benefit only under heavy
  concurrency; adds complexity (separate sync/async session stacks) that would
  have cost the team hours for zero demo-visible gain. Documented future
  option, not a mistake.
- **WHY the 2.0 style:** `select(Entity).where(...)` reads like SQL, is
  type-checkable, and is the modern documented style — no legacy
  `Query`-API baggage.

### 3.6 Alembic

- **WHAT:** migrations in `alembic/versions/89b3589cbecd_initial_schema.py`.
  `alembic/env.py` reads `DATABASE_URL` from `app.core.config.settings` so
  container/local/CI share one source of truth.
- **WHY:** schema-as-code that can be applied to a fresh DB in one command
  (`alembic upgrade head`) — the whole demo is `docker compose up`.
- **Hand-extension (important):** the initial migration was autogenerated,
  then manually extended with:
  - `CREATE EXTENSION IF NOT EXISTS pg_trgm` (trigram search)
  - the GIN trigram index on `employees.full_name`
  - the **partial unique index** `uq_contracts_one_running_per_employee`
  - two SQL views: `v_time_off_balances`, `v_working_schedule_hours`
  - the views are **not registered as ORM tables** (only read-only mappers in
    `app/models/views.py`, excluded from `models/__init__.py`) so autogenerate
    doesn't try to recreate/drop them. Re-autogenerating may show diff noise
    around views — expected, ignore it.

### 3.7 PostgreSQL 16

- **WHAT:** `postgres:16-alpine` in docker-compose, health-checked, with a
  named volume.
- **WHY Postgres over MySQL:** partial unique indexes (`WHERE status =
  'running'`), the `pg_trgm` extension for the employee search box, native
  ENUM types with DB-level validation, richer date/time, `EXCLUDE`
  constraints available for future hardening.
- **WHY Postgres over SQLite (the tests make this concrete):** the spec
  explicitly prefers a real Postgres test DB *"to catch constraint-dependent
  behavior like the partial unique index"* — CHECK constraints, native ENUMs,
  and concurrency semantics differ subtly; a green SQLite test suite that
  passes while the real DB rejects a second running contract would be
  worthless.
- **WHY over Mongo/NoSQL:** payroll is the definition of relational integrity
  — joins across employees/contracts/payruns/payslips, transactions that must
  be atomic (contract activation), and constraints that must be structural.
  Documents would make every one of those rules a hand-rolled app-level
  ceremony.

### 3.8 bcrypt directly — not passlib

- **WHAT:** `app/core/security.py` calls `bcrypt.hashpw`/`checkpw` directly
  with `rounds=12`.
- **WHY:** passlib 1.7.4 is **unmaintained and breaks with bcrypt ≥ 4.1** (a
  known incompatibility: passlib imports `bcrypt.__about__` which was
  removed). The code says it in a comment — using bcrypt directly avoids the
  whole class of breakage.
- **WHY NOT:** Argon2 (stronger, but another dep + binary; bcrypt at 12
  rounds is plenty for a demo), SHA-* (no salt, fast to brute-force).

### 3.9 PyJWT

- **WHAT:** HS256-signed tokens; access tokens (60 min) carry `type:
  access`, refresh tokens (7 days) carry `type: refresh`. `get_current_user`
  only accepts access tokens; the refresh flow re-issues a pair.
- **WHY:** tiny, standard, no surprises. `decode_token` raises `PyJWTError`
  subclasses which `get_current_user` translates to a 401 — clean.
- **WHY NOT python-jose / authlib:** heavier, more moving parts; PyJWT does
  everything needed. WHY NOT opaque session cookies: the SPA keeps the token
  in `localStorage` and sends `Authorization: Bearer` — no CSRF surface, no
  server session store.

### 3.10 pytest + httpx + anyio

- **WHAT:** `pytest.mark.anyio` tests run on the `asyncio` backend, driving
  the real app through `httpx.ASGITransport` with `get_db` overridden to a
  test Postgres.
- **WHY:** tests the *actual* app (routers, deps, exception handlers), not a
  mocked service; hits real constraint behavior. `httpx ≥ 0.28` dropped the
  context-manager protocol on `AsyncClient`, so the fixture closes it
  explicitly (comment in the code).

### 3.11 reportlab

- **WHAT:** `app/modules/payroll/pdf.py` renders payslip PDFs; served as
  `application/pdf` with `Content-Disposition: attachment`.
- **WHY:** pure-Python, zero system dependencies (no LaTeX, no wkhtmltopdf),
  fine-grained layout control for a legal-ish document.
- **WHY NOT WeasyPrint/HTML→PDF:** heavier install, slower; reportlab is the
  pragmatic choice for a fixed-format document.

### 3.12 Docker Compose

- **WHAT:** `db` (postgres:16-alpine, healthcheck) + `app` (build from
  Dockerfile). The app command is:
  `alembic upgrade head && python -m app.seed.seed_data && uvicorn ...`
- **WHY:** `docker compose up` is the entire demo — fresh DB, migrations,
  idempotent seed, running API at :8000 with docs at /docs. No per-developer
  setup drift.
- **WHY NOT:** Kubernetes/cloud (way overkill), a local-only setup (the
  README documents a former no-Docker launcher with embedded Postgres that was
  **deleted** because `pgserver` ships no contrib modules and the initial
  migration enables `pg_trgm` — the launcher's schema silently diverged from
  the real one. Docker or system Postgres is the only supported path.)

### 3.13 Frontend: React 19 + Vite + TypeScript

- **WHAT:** `frontend/` — Vite 8 + React 19 + TypeScript ~6 + react-router-dom
  7 + oxlint. A thin fetch wrapper (`src/api/client.ts`), typed response
  mirrors (`src/api/types.ts`), auth context (`src/auth.tsx`), a layout, and
  page components.
- **WHY Vite:** instant dev server, first-class TS, tiny config.
- **WHY oxlint over ESLint:** one dependency, fast, zero-config sensible
  rules — fits the "don't install the world" spirit.
- **Auth on the client:** token in `localStorage` (`pp360.access_token`),
  sent as Bearer; **any 401 triggers `logout()`** (clear token + full reload
  to `/login`); `RequireAuth` / `RequireAdmin` route guards.
- **Graceful degradation (important design decision):** the frontend was built
  by Ambuj while Ameen's API was still a stub, so `listEmployees()` catches
  404/405 and returns `null`, and the Employees screen shows an explanatory
  empty state instead of crashing. Response fields are read *leniently*
  (`asEmployee` normalizes `full_name` vs `name`, `department.name` vs
  `department_name`, etc.). This is deliberate, documented, and now that the
  API is live it can be tightened.

### 3.14 What's deliberately NOT in the stack

| Not used | Why not |
|---|---|
| Async driver (asyncpg) | Sync psycopg2 is simpler; no scale need (see 3.5) |
| Redis / Celery / background jobs | Single-container demo; the "EOD sweep" is a manual endpoint (`POST /attendance/sweep-missing-checkouts`) that stands in for a cron job |
| Message queue / event bus | Nothing async happens; contract activation is one DB transaction |
| Microservices | 4 people, 24 hours — a monolith with clean module boundaries wins; see 4.1 |
| UUID primary keys | `BIGSERIAL` — simpler, faster joins, no frontend need for non-guessable IDs (architecture doc §4.10 recommends this for a hackathon) |
| `citext` extension | Emails are `VARCHAR` + **app-level lowercasing on write** + unique constraint — identical login behavior, one less extension to manage |
| Stored leave balances / stored weekly hours | They're derived views — stored totals drift (see 5.5) |
| `eval()` for salary formulas | Restricted AST walker instead (see 9.1) — bare eval is a code-injection hole |
| Passlib | Unmaintained, breaks with modern bcrypt (see 3.8) |
| JSON web tokens in cookies | Bearer in localStorage is simpler for the SPA (see 3.9) |
| FLOAT/DOUBLE for money | `NUMERIC(12,2)` + `Decimal` everywhere — float rounding errors are unacceptable in payroll (architecture §4.9) |
| Hard DELETE anywhere | Soft delete via `is_active`/status — history is the product (see 4.7) |

---

## 4. Architecture

### 4.1 Monolith with vertical slices (the 4-person split)

- **WHAT:** One codebase, one DB, one FastAPI app. The work is split into 4
  **vertical domains**, each owned by exactly one person with a disjoint set
  of folders:

| Person | Owns |
|---|---|
| Eldo | `app/core/*`, `app/models/*` (ALL tables), `alembic/`, `app/main.py`, auth module, seed data |
| **Ameen** | `app/schemas/employee.py`, `app/modules/employees/**`, `tests/test_employees.py` |
| Ambuj | `app/schemas/attendance_timeoff.py`, `app/modules/attendance_timeoff/**`, tests, frontend |
| Steve | `app/schemas/payroll.py`, `app/modules/payroll/**`, tests |

- **WHY:** *"This system is split into 4 vertical domains. Each person owns a
  disjoint set of folders/files. Nobody edits another person's files."* The
  only shared files (`main.py`, `core/*`, `models/*`) are written once, early,
  by Eldo, then **frozen** — everyone else only imports.
- **WHY a monolith and not microservices:** microservices would multiply the
  shared contracts (network boundaries, auth propagation, transactions across
  services) for a team that needs to *demo a working payroll* in 24 hours.
  The module boundaries inside one app give 90% of the isolation with none of
  the operational cost.
- **How routers wire without touching shared files:** `app/main.py` does
  static, one-time imports of all five routers (auth, employees,
  attendance_timeoff, payroll, dashboard) with their prefixes. Each module
  owner replaced their empty `router = APIRouter()` stub — `main.py` was never
  touched again after Hour 1.

### 4.2 The golden rule: models are frozen and single-owner

> **"If you need a field/table that doesn't exist yet, do NOT edit
> `models/`. Post in the team chat: 'Eldo, please add column X to table Y.'"**

This is why Ameen's prompt says *"do not create or alter models; import and
use `app/models/organization.py` and `app/models/employee.py` as-is. If you
need a field that doesn't exist, flag it to Eldo."* Concretely: the employee
service *needs* `company_id` on master tables, and the schema has it
nullable with `company_id=1` defaulted in seeds — a deliberate multi-company
placeholder, not a per-company tenant model (see 5.2).

### 4.3 Layering: router → service → model

```
router.py   HTTP concerns only: path, params, Depends(require_roles(...)),
            status codes, response_model. Parse request → call ONE service
            function → return.
service.py  ALL business logic: validation, transactions, edge cases,
            friendly errors. Raises AppException subclasses.
models/*.py Read-only imports (frozen, Eldo's).
```

- **WHY:** *"All edge-case validation lives in the service layer so it's
  unit-testable without spinning up HTTP."* The payroll engine and schedule
  helpers are literally pure functions (`run_engine`, `derive_attendance`,
  `compute_total_weekly_hours`) tested without a DB.
- **WHY services never raise raw `HTTPException`:** keeps them importable
  without FastAPI and lets the global handler own the JSON shape.

### 4.4 Response envelope & status codes

- **WHAT:** Endpoints return **Pydantic response models directly** — no
  hand-rolled `{status, data}` wrapper. List endpoints use the pagination
  envelope `{"items": [...], "total": N, "page": P, "page_size": S}`.
- **WHY:** a generic wrapper complicates OpenAPI docs and frontend typing;
  HTTP status codes already carry the semantics. This is an explicit,
  documented decision (architecture §4.1) — every module follows it.
- **Status code usage:** 200 OK, 201 created, 204 deleted, 400 bad request,
  401 unauthenticated, 403 forbidden, 404 not found, 409 conflict (duplicate
  / state-machine violation / concurrent modification), 422 validation.

### 4.5 Error handling: the AppException hierarchy

```python
AppException          400  base
NotFoundException     404
ConflictException     409
ValidationException   422
ForbiddenException    403
UnauthorizedException 401
```

- Raised from services; one global handler in `app/core/exceptions.py`
  converts them to `{"detail": "...", "error_code": "..."}`.
- **Backstops:** `IntegrityError` → 409 ("uniqueness or referential
  constraint... check for duplicates") — so even if a service *forgets* to
  pre-check a duplicate, the client sees a clean conflict, never a 500 with a
  stack trace. Other `SQLAlchemyError` → 500. `RequestValidationError` → 422
  with the field errors.
- **WHY this matters for Ameen's module:** the contract edge cases are
  *supposed* to return 409/422 with friendly messages (e.g. "Cannot activate a
  contract with status 'expired'"), and the service layer does the friendly
  translation while the global handler is the safety net.

### 4.6 Pagination & filtering contract

- Query params: `?page=1&page_size=20&sort_by=<field>&sort_dir=asc|desc` plus
  domain filters (`department_id=`, `status=`, `search=`, ...).
- **Clamping (architecture §5.5):** `page >= 1`, `1 <= page_size <= 200`
  (default 20). `paginate()` in `app/modules/employees/service.py` is the
  canonical implementation; routers also add `Query(ge=1, le=200)` so FastAPI
  rejects garbage at the boundary too.
- Ameen's module has a **Kanban variant** of the envelope:
  `{"groups": [{key, count, items}], total, page, page_size}` — see 7.4.

### 4.7 Soft delete & auditing

- **WHAT:** master/transactional records (Employee, Contract, Department,
  JobPosition, WorkingSchedule, TimeOffType, Payrun, Payslip, User) are never
  hard-deleted — `is_active` flag or status transitions instead. Only pure
  config rows created accidentally may be hard-deleted.
- **WHY:** *"This preserves history required by the problem statement
  ('preserve finalized/paid payroll as historical records', 'retain contract
  history')."*
- **Auditing:** every table gets `created_at`/`updated_at` (server-side
  defaults via `TimestampMixin`), and status-changing actions stamp
  `*_by_user_id`/`approver_id` where the schema allows (attendance
  corrections, approvals, payrun `created_by_user_id`).

### 4.8 RBAC model (roles vs permissions)

Five roles from the problem statement:

| Role | Powers |
|---|---|
| `EMPLOYEE` | Read own records only; self check-in/out; own time-off requests |
| `HR_MANAGER` | Full CRUD on employees/attendance/contracts/schedules/time-off; approve; **NO payroll access** |
| `HR_PAYROLL_USER` | HR_MANAGER + create/read/update payruns & payslips; **read-only** salary structures/rules |
| `HR_PAYROLL_MANAGER` | Everything HR_PAYROLL_USER can + full CRUD on payruns, payslips, structures, rules |
| `ADMIN` | Everything + user/role management |

- **`require_roles(*roles)`** — OR across the user's roles (a user holds many
  roles via `user_roles`). This is the primary gate.
- **`require_permission(*codes)`** — finer-grained check through the
  `role_permissions` matrix (11 permission codes seeded, e.g. `payrun.write`,
  `salary_rule.write`, `user.manage`). Both styles exist and are used — the
  user-management endpoints use role checks.
- **Critical rule:** *users cannot assign or elevate their own roles* —
  `replace_user_roles` rejects when `actor.id == target.id` and the role set
  would change; `update_user_account` also blocks disabling your own account.
- **Self-service boundary (the pattern every module implements):** EMPLOYEE
  is scoped to their own `employee_id` (linked on `users.employee_id`) and any
  attempt to read/act on another employee's id → 403. Ameen's
  `_resolve_employee_for_read`, Ambuj's `_resolve_scope_employee` /
  `_resolve_list_scope` / `_resolve_request_employee`, and Steve's
  `can_access_payslip` all implement the same idea in each domain.

---

## 5. Data model deep dive (25 tables + 2 views)

### 5.1 ERD at a glance

```
companies 1─* departments 1─* job_positions
departments 1─* employees *─1 job_positions
employees 1─* (self) manager_id
employees 1─1 employee_bank_details
employees 1─* contracts *─1 salary_structures
employees 1─* attendances
employees 1─* time_off_allocations *─1 time_off_types
employees 1─* time_off_requests *─1 time_off_types
employees 1─1 users *─* roles (via user_roles) *─* permissions (via role_permissions)
working_schedules 1─* working_schedule_lines
working_schedules 1─* employees / contracts
salary_structures 1─* salary_structure_rules *─1 salary_rules
payruns 1─* payrun_employees *─1 employees
payruns 1─* payslips *─1 employees, *─1 contracts
payslips 1─* payslip_lines *─1 salary_rules
payslips 1─* payslip_warnings
```

### 5.2 Conventions (all applied across the 25 tables)

- **Naming:** a mandatory `NAMING_CONVENTION` on `Base.metadata` gives every
  constraint/index a deterministic name (`uq_`, `ck_`, `fk_`, `ix_`, `pk_`) —
  keeps Alembic autogenerate diffs clean across the team.
- **PKs:** `BIGSERIAL` autoincrement. **WHY NOT UUID:** simpler/faster for a
  hackathon; no frontend need for non-guessable IDs (§4.10).
- **Timestamps:** `TIMESTAMPTZ` (UTC) `created_at`/`updated_at`, server-side
  defaults. **Pure dates** (`date_of_joining`, `period_start`, leave ranges)
  are `DATE`, never timestamp — date math across payroll periods must not
  carry time-of-day noise.
- **Money:** `NUMERIC(12,2)` (or `NUMERIC(6,2)` for hours/durations), never
  float. Python side: `Decimal` everywhere; quantized with `ROUND_HALF_UP`.
- **FK policies:** mostly `ON DELETE RESTRICT` (protect history — you can't
  delete a department that contracts reference), with deliberate exceptions:
  `SET NULL` for soft/optional links (manager, parent dept, approver,
  `users.employee_id`) and `CASCADE` for owned children (schedule lines,
  payslip lines, junction rows, bank detail).
- **`company_id`:** nullable FK on every master table except `companies`
  itself — a multi-company *placeholder* (seeds default to company 1), not a
  full tenant model. Documents the intent without building multi-tenancy it
  doesn't need.
- **Every FK is indexed** (Postgres doesn't auto-index FKs) — plus composite
  and special indexes (see 5.8).

### 5.3 Enums (PG-native)

Closed sets stored as **Postgres native ENUM types** (`employeetype`,
`employeestatus`, `contractstatus`, `attendancestatus`, `schedule_type`,
`allocationstatus`, `timeoffrequeststatus`, `salaryrulecategory`,
`computationmethod`, `payrunstatus`, `payslipwarningtype`, `time_off_unit`).

- **WHY native ENUMs:** DB-level validation for free — a bad string can't
  reach the table even if the app layer slips. Shared `name=` means one PG
  type is reused where sensible (e.g. `employee_type` and
  `payrun.employee_type_filter` share `employeetype`).
- **WHY NOT plain VARCHAR + app validation:** DB-level enforcement is the
  whole point — the partial unique index's `WHERE status = 'running'` only
  works because `status` is a typed, comparable value.

### 5.4 Intentional denormalization — what IS stored and why

Two documented cases (architecture §5 / README):

1. **`payslip_lines.code/name/category` snapshot the salary rule at compute
   time.** Editing a Salary Rule next month must **not** retroactively rewrite
   a paid payslip — payslips are legal/historical records. The line keeps its
   own copy of the rule's identity; only `salary_rule_id` still points at the
   (possibly edited) rule for traceability.
2. **`attendances.worked_hours` + `.status` are computed at check-out and
   stored.** A later change to the employee's working schedule must **not**
   silently rewrite historical attendance. Corrections are *stamped* as
   manual corrections (`is_manual_correction`, `corrected_by_user_id`), never
   silently re-derived.

The counter-examples (what's deliberately NOT stored) are in the next
section — the pattern is: *snapshot what is legally/historically fixed;
derive what is a live running total.*

### 5.5 Deliberately NOT stored — the SQL views and why

1. **Leave balance** → `v_time_off_balances`:
   `SUM(approved allocations) − SUM(approved requests)` per employee+type,
   live. **WHY NOT a stored running total:** *"A stored running total would
   drift out of sync"* — every approval/refusal/cancellation would need a
   compensating update, and any missed one silently corrupts the balance
   (worse than wrong: it *looks* right). The view is the single source of
   truth; approving a request needs **no deduction write** — the view moves.
2. **Weekly schedule hours** → `v_working_schedule_hours`
   (`EXTRACT(EPOCH FROM SUM(end_time - start_time - make_interval(mins =>
   break_minutes)))/3600` per schedule), plus a Python convenience property on
   `WorkingSchedule` for already-loaded rows.

The view mappers live in `app/models/views.py` as read-only ORM classes,
deliberately **excluded from `app/models/__init__.py`** so Alembic
autogenerate never tries to manage them as tables.

### 5.6 The one-running-contract partial unique index

```sql
CREATE UNIQUE INDEX uq_contracts_one_running_per_employee
  ON contracts (employee_id) WHERE status = 'running';
```

- **WHY partial:** the rule is not "one contract per employee" (history has
  many drafts/expired) — it's *"at most one **running** contract"*. A partial
  unique index expresses exactly that, structurally. A second running contract
  is **impossible** at the DB level, no matter what the app does.
- **Service layer cooperation:** `activate_contract` expires the current
  running contract *in the same transaction* before marking the target
  running, so the index normally never fires. `_commit_or_conflict` catches
  the race (`IntegrityError`/`StaleDataError`) and translates it to a clean
  409 — belt-and-suspenders, exactly as the spec demands ("a concurrent
  request could race you, which is exactly why the DB constraint exists too").
- **Future hardening (documented, deliberately skipped for the hackathon):** a
  full `btree_gist` `EXCLUDE` constraint would also reject *overlapping
  date-range* contracts of *different* statuses (e.g. two expired contracts
  covering the same period). Not needed for the demo.

### 5.7 Optimistic locking (`version_id`)

- High-contention tables — **Contract, Payrun, Payslip, TimeOffAllocation** —
  carry `version_id` (`server_default=1`) with
  `__mapper_args__ = {"version_id_col": version_id}`.
- **WHY:** two users editing the same record simultaneously; last-write-wins
  silently overwrites one person's work. The `version_id` is returned in
  reads; writes send it back; a stale version → **409 "This contract was
  modified by someone else. Please refresh and try again."**
- **How it works in SQLAlchemy:** on UPDATE, the mapper adds
  `WHERE version_id = <held value>` and increments it; if zero rows match it
  raises `StaleDataError`, which `_commit_or_conflict` converts to the 409.
  Ameen's contract `PATCH`/`activate`/`expire`/`cancel` all take
  `ContractActionRequest {version_id}`.
- **WHY NOT pessimistic locking (`SELECT ... FOR UPDATE`):** holds DB
  connections open during user think-time; optimistic locking is simpler and
  the failure mode (refresh and retry) is friendlier for an HR tool.

### 5.8 Indexing strategy

- Every FK → B-tree index (explicitly in the migration).
- `contracts`: **partial unique** `(employee_id) WHERE status='running'`.
- `employees.full_name`: **GIN trigram** (`pg_trgm`) for the search box —
  substring search, not just prefix (`%ameen%` matches "Ameen Shaikh").
  Requires `CREATE EXTENSION pg_trgm` in the migration.
- `employees`: `(status)`, `(employee_type)`, `(department_id)`,
  `(job_position_id)`, `(manager_id)` — the dashboard filter set.
- `attendances`: composite `(employee_id, check_in)` (the dashboard's hottest
  query: attendance for X in date range Y) + `(status)`.
- `time_off_allocations` / `time_off_requests`: composite
  `(employee_id, time_off_type_id, status)` / `(employee_id, status)` +
  `(time_off_type_id)` — the balance-lookup hot path.
- `payslips`: unique `(payrun_id, employee_id)` (duplicate payslips
  structurally impossible) + `(employee_id, period_start, period_end)` for
  overlap checks.
- `payruns`: `(status)`, `(period_start, period_end)`.
- `salary_structure_rules`: `(salary_structure_id, sequence)` — the engine
  reads rules in sequence order.

---

## 6. Business logic — Auth & RBAC (Eldo's)

`app/modules/auth/` — login, refresh, `/me`, admin user CRUD.

- **Login** (`POST /auth/login`): OAuth2 password flow — the `username` field
  carries the email. `authenticate_user` verifies bcrypt + `is_active`, stamps
  `last_login_at`, and returns access+refresh tokens. **Never reveals which
  part failed** ("Incorrect email or password." — enumeration resistance).
- **Refresh** (`POST /auth/refresh`): validates `type: refresh` claim,
  re-issues a fresh pair. Refresh tokens are 7 days; access 60 minutes.
- **`GET /auth/me`**: current user + roles + linked employee — the frontend
  bootstraps auth state from this.
- **User management** (ADMIN only): create user (email uniqueness 409,
  **one-account-per-employee** 409, password ≥ 8 chars), replace roles
  (**self-elevation blocked** 403), and `PATCH /users/{id}` to
  link/unlink an employee or disable an account — this is how an EMPLOYEE
  account created without a profile gets fixed after the fact.
- **Why it matters to Ameen:** `users.employee_id` is the linchpin of the
  whole "EMPLOYEE sees only themselves" story — `GET /employees/me` resolves
  through it, and 404s when it's NULL (the seeded admin has no linked
  employee, on purpose, to demonstrate this).

---

## 7. Business logic — Employee module (YOURS, Ameen)

`app/schemas/employee.py` · `app/modules/employees/**` · `tests/test_employees.py`
— Departments, Job Positions, Working Schedules, Employees, Contracts.

### 7.1 Departments — `service_department.py`

CRUD with a self-referencing hierarchy (`parent_department_id`).

- **Create:** parent must exist (404) **and be active** (422); duplicate
  `(name, company_id)` → 409 with a friendly message (pre-checked, not a raw
  IntegrityError).
- **Update (PATCH):** all-optional; the tricky part is the parent change:
  - self-reference → 422 ("A department cannot be its own parent.")
  - **cycle detection** (A→B→A) → 422. Implemented by `would_create_cycle` in
    the shared `service.py`: walk the parent chain from the *new* parent up to
    `MAX_HIERARCHY_DEPTH = 20`; if you reach the node being reparented, it's a
    cycle. **Why the depth cap:** corrupt data could create an infinite parent
    loop — the cap turns an infinite loop into a 422.
  - duplicate-name check excludes the row itself (rename is fine).
- **Delete = soft delete** (`is_active=false`). But first: count **active
  employees** and **active job positions** under it; if either is non-zero →
  **409 with the counts in the message** ("Cannot deactivate department 'X':
  it still has 1 active employee(s) and 2 active job position(s). Reassign or
  deactivate them first."). **Why:** a silent cascade would orphan employees
  and corrupt payroll references; the counts tell HR exactly what to fix.
- **Why hierarchy at all:** dashboard roll-ups (Steve's) aggregate by
  department tree — the self-FK supports parent/child grouping.

### 7.2 Job Positions — `service_job_position.py`

Same CRUD shape, scoped to a department.

- `department_id` must exist and be active (404/422).
- Duplicate `(title, department_id)` → 409.
- **Soft delete** → 409 with count if any **active** employees are still
  assigned to the position (same "reassign first" pattern as departments).

### 7.3 Working Schedules — `service_schedule.py`

A schedule = header (`name`, `schedule_type`) + weekly pattern **lines**
(`day_of_week` 0=Mon..6=Sun, `start_time`, `end_time`, `break_minutes`),
normalized to `working_schedule_lines` (3NF).

- **`total_weekly_hours` is DERIVED, never stored.** Three implementations
  exist:
  - the SQL view `v_working_schedule_hours` (bulk aggregation),
  - the ORM float `property` on `WorkingSchedule` (convenience),
  - **`compute_total_weekly_hours()` — the pure function in `service.py`**
    (`sum((end - start) − break)` in minutes → `Decimal` hours, quantized to
    2dp) which the module actually serializes with.
  - **Why the pure function wins for JSON:** consistency — `"40.00"`, not
    `"40.0"` (float property would produce the latter). Pure = unit-testable
    with zero DB (see `test_compute_total_weekly_hours_pure`).
- **Line validation** (`validate_schedule_lines`, all → 422 with friendly
  messages, DB CHECKs backing up 2 of 3):
  1. `day_of_week` in 0–6 (DB CHECK `ck_..._day_of_week_range` backs it up),
  2. `end_time > start_time` (DB CHECK `ck_..._end_after_start` backs it up),
  3. `break_minutes < shift duration` (else hours would go negative — *not* a
     DB constraint, service-only),
  4. **no same-day overlapping ranges** (sorted adjacent-pair check — *not* a
     DB constraint, service-only; Postgres can't express "intervals on the
     same day must not overlap" without an EXCLUDE constraint).
- **PUT `/working-schedules/{id}/lines` replaces the FULL set atomically.**
  **Why replace, not incremental add/remove:** *"simplest correct semantics —
  avoids partial-update ordering bugs."* Validation runs **before** any
  mutation, so a bad payload leaves the schedule untouched (tested).
- **Zero-line schedules:** allowed to exist (`total_weekly_hours = 0.00`,
  draft-ish) but **not assignable** — `validate_schedule_assignable` requires
  active **and** ≥ 1 line, else 422 "assign at least one working day first".
  **Why allow them at all:** HR might build a schedule in two steps
  (create header now, add lines later).
- **Soft delete** → 409 with counts if referenced by **active employees** or
  **draft/running contracts**.

### 7.4 Employees — `service_employee.py`

The central hub — Kanban + List + Form from one endpoint.

**List + Kanban in one endpoint.** `GET /employees` returns the standard
`Paginated` envelope, but with `?group_by=status|department` it switches to
`GroupedList` (`groups: [{key, count, items}]`).

- **WHY return the FULL filtered set when grouped instead of paging:** *"a
  board needs all columns, not one page"* — each Kanban column's `count` must
  be exact, and paging would corrupt it.
- Filters: `department_id`, `status`, `employee_type`, `manager_id`,
  `search` (ilike on `full_name` + `work_email`; the GIN trigram index makes
  the DB-side search fast too). Sorting whitelisted to `SORTABLE_FIELDS`
  (default `full_name asc`) — unknown `sort_by` falls back instead of
  erroring, and `sort_dir` is regex-constrained in the router.

**Detail (Form payload)** — `_build_detail` assembles: identity fields +
nested `department`/`job_position`/`manager` (**id + name only, "not full
object, to avoid N+1 over-fetching"**) + `working_schedule` (id, name, type,
`total_weekly_hours`) + **smart-button counts** (`related: {contracts_count,
attendance_count, time_off_count, allocations_count}`) + `warnings`.

- **Warnings field:** `["manager is inactive"]` when the manager's status ≠
  active. **Why allow an inactive manager instead of blocking:** *"HR
  sometimes intentionally does this during transitions."* The response tells
  the truth without blocking the workflow.
- **Why `_build_detail` does ~6 explicit lookups instead of ORM
  relationships:** the `Employee` model deliberately has **no** relationship
  attrs for department/job/schedule (Eldo's schema is FK-column-centric), so
  the service fetches each and assembles dicts. Fine for single-record views.

**Create:** active department (422), active job position (422), assignable
schedule (422), manager exists (404); **`work_email` lowercased on write** +
duplicate pre-check → 409 (the DB unique constraint + global IntegrityError
handler are the backstop). Creates **only the Employee row** — contracts are
created separately through §7.5. **Why:** an employee can exist with zero
contracts (Kiran in the seed does exactly this and it must not crash
payroll).

**Update (PATCH):** partial; `model_fields_set` distinguishes absent from
explicit-null (so `manager_id: null` clears, absence leaves unchanged).

- `work_email` re-lowercased + duplicate check **excluding self** → 409.
- `department_id`/`job_position_id`/`working_schedule_id` must be
  **active** → 422.
- `manager_id` == self → 422; cycle in the management chain → 422 (same
  `would_create_cycle` walk, capped at 20).
- **Critical rule:** the PATCH touches **only the Employee row**. Contracts
  snapshot department/job/schedule **at creation time** — changing an
  employee's department never retroactively rewrites past contracts (tested:
  `test_employee_update_touches_only_employee_row`).

**Delete vs Terminate (two distinct actions):**

- `DELETE /employees/{id}` → soft delete: `status = 'inactive'`.
- `POST /employees/{id}/terminate` → explicit, auditable `status =
  'terminated'` (409 if already terminated). **Why a dedicated action instead
  of overloading DELETE:** termination is a legal event with meaning; a plain
  DELETE that happens to set a status hides that intent from audit.

**Self-service (EMPLOYEE role):**

- `GET /employees/me` — resolves `current_user.employee_id`; **404 if no
  linked employee** (admin accounts demonstrate this).
- `GET /employees/me/contracts` — own contract history.
- `GET /employees/{id}` / `/{id}/contracts` / `/{id}/related-summary` —
  EMPLOYEE gets **their own** record, **403 for anyone else's**
  (`_resolve_employee_for_read`).
- `GET /employees` (list) → **403** for EMPLOYEE. **Why:** *"they only get
  /me"* — a list would leak the whole org.

**Route ordering gotcha (documented in the router):** `/employees/me` and
`/employees/me/contracts` are declared **before** `/employees/{employee_id}` —
otherwise FastAPI would parse `"me"` as an int and return 422 instead of the
intended route. Same pattern appears in Ambuj's (`/attendance/me`,
`/time-off/balances/me`) and Steve's (`/payslips/me`) routers.

### 7.5 Contracts — `service_contract.py`

The most business-logic-dense file in the module. The contract is a
**lifecycle state machine** with **append-only history**:

```
draft ──activate──▶ running ──expire──▶ expired
  │                    │
  └──cancel──▶ cancelled   (expire/cancel only from the listed states; PATCH only while draft)
```

**Creating** (`POST /contracts`): employee must be **active** (422 — no
contracts for inactive/terminated staff; historical backfill is an Admin
flag), all FKs validated exist+active (`_validate_contract_fks`), `end_date
>= start_date` (422), auto-generated `contract_number`
(`CON/{year}/{seq:04d}` where seq = max(id)+1 — simple, demo-grade; NOT
guaranteed gapless under concurrency, which is acceptable for display
purposes). **Status is always forced to `draft`** — there is no way to create
a running contract.

**Editing** (`PATCH /contracts/{id}`): **draft only**. Editing
running/expired/cancelled → **409** with the guidance: *"To change terms,
create a new draft contract with the new wage and activate it — history is
preserved that way."* Optimistic lock via `version_id` (stale → 409).
`end_date: null` is legal (clearable).

**`POST /contracts/{id}/activate` — THE centerpiece.** One DB transaction:

1. Target must be `draft` (else 409).
2. Find the employee's current `running` contract, if any.
3. If one exists:
   - **Reject 422 if `running.start_date >= target.start_date`** — activating
     out of chronological order is illegal; the caller must pick a later
     start date. (Equal dates are also out of order — tested.)
   - Expire it: `running.end_date = target.start_date - 1 day`, but **only
     tightening** (if it already ends earlier or is open, don't extend it —
     keeps `ck_contracts_end_after_start` honest and never widens a past
     range).
4. Mark the target `running`. Commit atomically.

**Why the no-gap/no-overlap dance:** payroll needs exactly one applicable
contract per period. If contract A runs 2024-01-15 → open and B starts
2025-01-15, activating B sets A's `end_date = 2025-01-14` — clean handoff.

**Race safety:** two people activating two drafts for the same employee
simultaneously → the transaction logic + the partial unique index ensure only
one wins; the loser's `_commit_or_conflict` turns the `IntegrityError` /
`StaleDataError` into a 409 "modified concurrently... please refresh".

**Future-dated activation is ALLOWED** (pre-scheduled contracts are normal) —
but see the resolver below: `status='running'` means "the current/next
contract in force", and the real eligibility filter is the date range.

**`POST /contracts/{id}/expire`:** running → expired; sets `end_date = today`
**only if `start_date <= today`** — a future-dated running contract that never
started keeps `end_date NULL` rather than getting an end date before its
start. Idempotency guard: expiring a non-running contract → 409.

**`POST /contracts/{id}/cancel`:** draft → cancelled (history preserved).

**NO DELETE endpoint — ever.** *"only `cancel` (draft) or `expire` (running)
exist, preserving history per the spec's 'retain contract history'
requirement."*

**`get_applicable_contract(employee_id, period_date)` — the resolver Steve's
payroll depends on:**

```python
Contract.employee_id == id
AND Contract.status == running
AND Contract.start_date <= period_date
AND (Contract.end_date IS NULL OR Contract.end_date >= period_date)
```

A pre-scheduled (future start) running contract is **not** applicable to an
earlier period — tested (`test_activate_future_start_allowed_and_resolver`).
Steve's engine has its own, richer resolver for whole periods
(`resolve_applicable_contract` — see 9.3) that also considers *expired*
contracts, because a **past** payrun period should use the contract that was
in force then.

### 7.6 Serialization & the N+1 problem

`_read_dicts` is the batch serializer for contract lists: it collects all
distinct `employee_id`s, `department_id`s, `job_position_id`s,
`working_schedule_id`s, and `salary_structure_id`s from the page, then runs
**5 queries total** (one `IN (...)` per entity, plus one for schedule lines
grouped by schedule id) — not 5 queries **per row**. **Why it matters:** the
contract list endpoint is the "smart button" powering employee dashboards; a
page of 20 contracts at ~6 queries each = 120+ queries vs ~6. Same care
applies to the schedule lines (batched via `defaultdict` grouping).

### 7.7 Files you own and why they're split that way

```
app/schemas/employee.py            all Pydantic models for the 5 entities
app/modules/employees/
  router.py                        combines the 5 sub-routers (main.py imports this)
  router_department.py / service_department.py
  router_job_position.py / service_job_position.py
  router_schedule.py    / service_schedule.py
  router_employee.py    / service_employee.py
  router_contract.py    / service_contract.py
  service.py                       shared pure helpers (pagination, weekly-hours,
                                   line validation, cycle detection, RBAC check)
tests/test_employees.py
```

**Why split per entity:** each file stays small, and the module is merge-safe
even within your own branch. **Why the shared `service.py` exists:** the
entity services import *from* it (never the reverse) so there are no circular
imports — it holds only cross-cutting, pure helpers. Note `HR_ROLES =
{HR_MANAGER, HR_PAYROLL_USER, HR_PAYROLL_MANAGER, ADMIN}` — EMPLOYEE
deliberately absent — drives every router's `require_roles(*HR_ROLES)`.

---

## 8. Business logic — Attendance & Time Off (Ambuj's)

`app/modules/attendance_timeoff/` — the module with the most *documented*
design decisions (the service docstring is worth reading in full; highlights):

**Attendance status derivation is a pure function** (`derive_attendance` over
a `ScheduleSpec` built from the employee's schedule line for that weekday):

- `late` → check-in wall-clock past scheduled start + grace (default 15 min,
  env-overridable `ATTENDANCE_LATE_GRACE_MINUTES`).
- `overtime` → not late AND worked > expected + threshold (default 0.5h).
- `present` → otherwise. **Priority: late > overtime > present.**
- `absent` → **never written**. A missing row IS the absence; dashboards and
  `/summary` derive it as `expected_schedule_days − attended_days`. No
  synthetic absent rows — avoids the "who manufactured this row?" ambiguity.
- `missing_checkout` → assigned by the EOD sweep (open rows whose expected
  end-of-day + 120 min grace has passed). Reads show it **lazily**
  (`_effective_status` upgrades open rows on the fly) without rewriting
  history; `POST /attendance/sweep-missing-checkouts` stamps rows for real —
  the manual stand-in for a scheduled job.
- **Overnight shifts** (23:58 → 00:15): worked hours are a full `timestamptz`
  delta, so they're correct; only wall-clock-vs-schedule comparisons (late,
  EOD) depend on the documented UTC-frame simplification (naive datetimes
  assumed UTC; aware normalized to UTC).
- **Break subtraction** happens only when the session plausibly spans the full
  break (elapsed ≥ break length) — a 30-minute partial shift contains no lunch.
- **Double check-in guard:** an open row (`check_out IS NULL`) blocks a new
  check-in (409) — can't be a DB constraint, so the service owns it.

**Time Off:**

- **Types:** readable by everyone (the request-form dropdown needs it), writes
  HR-only. Deactivating/deleting a type with **pending** (`to_approve`)
  requests/allocations → 409 until resolved — *"simpler and safer than
  grandfathering in-flight rules."*
- **Allocations:** HR grants, created as `to_approve`, then approve/refuse
  (state machine with 409 idempotency guards; optimistic lock via
  `version_id`).
- **Balances:** `v_time_off_balances` is the single source of truth (see
  5.5). Request approval checks the SAME query (`remaining_balance`) — no
  separate deduction write. The 409 detail carries the math: "Requested 6.00
  but only 3.50 remaining."
- **Requests:** EMPLOYEE submits own (employee_id forced), HR may create on
  behalf; approve guards overlap + live balance in one transaction; cancel
  rules: requester cancels own `to_approve`; HR may cancel an APPROVED
  request but only before `date_from` has passed.
- **Approver stamping:** `approver_id = current_user.employee_id` when the
  user has a linked employee; admin-only accounts (no linked employee) leave
  it NULL — audit history stays intact, columns are nullable for exactly this.

---

## 9. Business logic — Payroll (Steve's)

### 9.1 The salary rule engine — no bare eval

`app/modules/payroll/engine.py` — the heart of the module.

**Rule model:** a `SalaryRule` is one of:
- `fixed` (an `amount`),
- `percentage` (a `percentage` of another rule's computed amount via
  `percentage_base_code`),
- `formula` (free-text math expression).

The DB CHECK `ck_salary_rules_method_consistency` enforces exactly one of
amount/percentage/formula per rule, matching `computation_method`. A
`SalaryStructure` includes rules **in execution order** via the
`salary_structure_rules` junction (`sequence`) — that's how "form view
manages included salary rules and their execution sequence" is modeled.

**Formula evaluation is a restricted AST walker, not `eval()`:**

- `ast.parse(expression, mode="eval")` then a recursive `_eval_node` that
  allows only: `Name` (looked up in the rule context — never globals),
  `Constant` (floats in stored formulas like `0.12` are converted to exact
  `Decimal(str(...))` — *"a raw float would smear binary noise into money
  math"*), `BinOp` (+ − * / // %), `UnaryOp`, `Compare`, `BoolOp`, and calls
  to a **whitelist**: `min, max, round, abs, sum` (no keyword args).
- Anything else → `PayrollEngineError` → becomes a payslip warning, amount 0.
- **WHY NOT bare eval:** code injection — a stored formula is text in the DB;
  `eval` would hand an attacker Python. The AST walker is the standard
  sandbox-by-construction approach.

**The engine loop (`run_engine`):** pure function (no DB) over ordered rules
with an injected base context:

```
CONTRACT_WAGE       applicable contract's wage_monthly (0 if none)
WORKED_DAYS         distinct attendance days present/late/overtime in period
TOTAL_WORKING_DAYS  expected working days from the schedule's weekday lines
PAID_LEAVE_DAYS     approved day-unit leave days with affects_payroll=true
UNPAID_LEAVE_DAYS   the same with affects_payroll=false
```

Per-rule failure (forward reference, unknown base code, bad formula, division
by zero) → **warning + amount 0, engine keeps going** — a payrun never crashes
because one rule is misconfigured.

- Gross = explicit `gross`-category rule, else sum of basic+allowance
  (+gross-category lines).
- Net = explicit `net`-category rule, else gross − deductions (+ a warning
  "Structure has no explicit NET rule").
- **Negative net → `negative_net` warning that BLOCKS validation** — you
  can't pay someone negative money by accident.
- Every amount quantized to 2dp with **`ROUND_HALF_UP`** (payroll
  convention).

The seeded "Regular Salary" structure shows it end to end:
`BASIC = 100% × CONTRACT_WAGE` → `HRA = 40% × BASIC` → `MEAL_ALLOWANCE`
(fixed) → `PF_DEDUCTION = 12% × BASIC` → `GROSS` (formula) → `NET` (formula).

### 9.2 The payrun lifecycle

```
draft ──compute──▶ computed ──validate──▶ validated ──mark-paid──▶ paid
  │                                                      │
  └──────────────cancel (draft|computed only)────────────┘
```

- **2-step wizard:** `POST /payruns/draft-scope` collects scope
  (period, department filter, employee-type filter, structure) and returns the
  **eligible employee list without creating a row**; `POST /payruns` then
  creates the draft payrun + the **explicit employee selection** in
  `payrun_employees` (wizard Step 2's whole point).
- **Compute** — idempotent: re-running replaces payslip lines on
  draft/computed payslips, skips finalized ones and reports them. No
  double-lines from clicking twice (architecture §5.7).
- **Validate** — blocked (409) while any **blocking warning** is open
  (`negative_net`, `missing_contract`); non-blocking warnings (e.g. "no
  explicit NET rule") don't stop it.
- **Mark paid** — only from `validated`; a second call → 409 (idempotency
  guard).
- **Cancel** — only from draft/computed; validated/paid runs are historical
  records.
- **Send payslips** — only from validated/paid (*"a DRAFT-watermarked PDF
  must not be emailed as final"*); per-employee results (already sent /
  missing bank details / missing work email), **never all-or-nothing**; the
  `SENT_AT` sentinel warning makes it idempotent — no double emails.
- **`UNIQUE(payrun_id, employee_id)`** on `payslips` makes duplicate payslips
  per payrun structurally impossible; overlapping *different* payruns is a
  service-layer query + `overlapping_period` warning.
- **`payslips.contract_id` is NULLABLE on purpose** — an employee selected
  with no applicable contract still gets a payslip carrying a
  `missing_contract` warning and zero salary, so payroll can't silently drop
  people.
- **RBAC is the most nuanced split in the system:** `HR_MANAGER` is excluded
  from every payroll route (they can manage people, not money);
  `HR_PAYROLL_USER` reads + runs payruns but **cannot edit salary
  structures/rules** (read-only); only `HR_PAYROLL_MANAGER`/`ADMIN` write
  structures. `EMPLOYEE` gets only `GET /payslips/me` and their own
  `/payslips/{id}/pdf` (403 otherwise, via `can_access_payslip`).

### 9.3 The contract resolver — how Steve depends on YOUR data

`resolve_applicable_contract(db, employee_id, period_start, period_end)` in
`engine.py` — the reason Ameen's "one running contract" rule must be airtight:

- Considers contracts with status `running` **or `expired`** — a **past**
  payrun period should use the contract that was in force then, even though
  it's now expired.
- A contract is applicable when its `[start_date, end_date-or-open]` range
  **overlaps the payroll period**.
- If several overlap (a mid-period contract change — legal), pick the one
  covering the **majority of the period** and record a "contract change"
  warning.
- No overlap → `missing_contract` warning, zero-value payslip, Validate
  blocked. The seed deliberately includes **Kiran (no contract)** and
  **Sneha (no bank details)** to demo exactly this.

This is the "smart button" integration point between your module and Steve's:
your `get_applicable_contract` (single date) is the simple resolver; his
period-based resolver is the richer one, and they agree on the core rule —
`status='running'` means "current/next in force", dates decide actual
eligibility.

---

## 10. Cross-cutting edge cases

The architecture doc §5 lists eight recurring traps; here's where each is
handled in code:

1. **Concurrent writes** → optimistic `version_id` on Contract/Payrun/
   Payslip/TimeOffAllocation; `_commit_or_conflict` / `_commit_with_lock`
   translate `StaleDataError`/`IntegrityError` → 409.
2. **Inactive/soft-deleted parents** → `require_active` on every referenced
   FK (department, job position, schedule, structure); creating a contract for
   a non-active employee → 422.
3. **Referential existence** → `get_or_404` + `require_active` on every FK in
   request bodies; global IntegrityError → 409 backstop.
4. **Empty/partial data** → no-contract employee still gets a payslip with
   `missing_contract` warning; no-schedule falls back to expected-days 0
   (engine guards the division); zero-line schedule exists but isn't
   assignable.
5. **Pagination edge cases** → `paginate()` clamps page ≥ 1, size 1–200;
   routers also declare `Query(ge=1, le=200)`.
6. **Timezone/date boundaries** → timestamptz storage, full-delta hour math
   (overnight shifts), period range inclusive both ends; documented UTC frame.
7. **Idempotency of bulk actions** → Compute replaces lines, Send-payslips
   uses a sentinel, mark-paid/validate guard their source state, activate
   expires-then-marks in one transaction.
8. **Authorization boundary leaks** → every "get by id" in every module
   checks ownership for non-HR roles; EMPLOYEE → 403 on any other id.

---

## 11. Testing strategy

- **Framework:** pytest + `httpx.ASGITransport` + `anyio` (asyncio backend).
- **DB:** real Postgres via `TEST_DATABASE_URL` (default
  `localhost:5433/peoplepay_test` — the README documents the throwaway
  container command). **Why not SQLite:** the partial unique index and CHECK
  constraints must actually fire (see 3.7).
- **Isolation:** session-scoped `Base.metadata.create_all` (after creating
  `pg_trgm`), then **`TRUNCATE ... RESTART IDENTITY CASCADE` between every
  test** — clean slate, sequences reset.
- **App wiring:** `app.dependency_overrides[get_db]` points at the test
  session; tests hit the real routers through ASGI, so deps, exception
  handlers, and RBAC all run for real.
- **Coverage per spec §4 (Ameen's):** create-chain 201s, duplicate email 409,
  activate-second-expires-first (with `end_date = start − 1` assertion and
  exactly-one-running DB check), out-of-order activation 422, EMPLOYEE 403/me
  200, overlapping schedule lines 422 (plus contiguous-ranges-not-overlap
  201), department self/two-hop cycles 422, soft-delete-with-active-employees
  409-with-counts — plus every §2.5 contract edge (wage PATCH on running 409,
  stale version 409 + correct version bumps, expire sets end_date=today,
  cancel draft-only, future-start activation + resolver behavior, inactive
  employee contract 422), schedule edges (zero-line, inverted times, day 7,
  atomic replace, in-use delete), employee edges (cross-employee 403, inactive
  manager warning, management cycle, terminate idempotency, PATCH-doesn't-
  touch-contracts, list filters + Kanban grouping, related-summary counts,
  duplicate dept name, inactive schedule), and the pure weekly-hours unit
  tests.

---

## 12. Frontend deep dive

- **Boot:** `main.tsx` → `App.tsx` routes: `/login`, then an auth-guarded
  `Layout` shell with `/employees`, `/attendance`, `/time-off/requests`,
  `/time-off/balances`, `/time-off/types`, `/time-off/allocations`, and
  ADMIN-only `/accounts`. `RequireAuth` blocks on loading, redirects to
  /login when logged out; `RequireAdmin` redirects non-admins to `/`.
- **API layer (`src/api/client.ts`):** a single typed `request<T>()` wrapper
  — attaches Bearer token, JSON/form encoding, **401 → logout + reload**,
  error normalization into `ApiError` with the backend's `detail`. Typed
  endpoint helpers per domain; `queryString()` drops empty params.
- **Auth (`src/auth.tsx`):** context holding `user` (from `/auth/me`) +
  `loading`; login stores the token then re-fetches `/me`.
- **Employees page** consumes Ameen's endpoints leniently via `asEmployee`
  normalization (see 3.13) and renders a null-API state until the contract
  matches — now that the API is live, the lenient readers can be tightened to
  the exact `EmployeeListItem` shape.
- **Styling:** plain CSS (`index.css`) — no component library. **Why:** 24h
  budget, fewer deps, and the wireframe's visual language is simple.

---

## 13. Seed data & demo flows

`python -m app.seed.seed_data` (idempotent — guarded by the OXP company row):

- 1 company (OXP), 5 departments, 10 job positions, 2 schedules
  (Full-Time 40h, Part-Time 20h), 18 employees, 7 users covering all 5 roles.
- Contract history (expired + running) for Aarav, Priya, Karan;
  terminated/inactive staff carry only expired contracts. **Kiran has NO
  contract**, **Sneha has NO bank details** — both surface as payslip
  warnings on Compute (intentional demo of the warning system).
- ~3 weeks of attendance per active employee, including `late`, `overtime`,
  `missing_checkout`, and one HR manual correction.
- 4 time-off types; allocations + a mix of approved / to_approve / refused
  requests; live balance via the view.
- **Payruns:** August 2026 (`paid`, 15 payslips with lines + Sneha's warning)
  and **September 2026 (`draft`, employees pre-selected, awaiting Compute —
  that's where the demo shows `missing_contract`/`missing_bank_details`
  warnings).**

Demo logins (password `Password@123`): `admin@oxp.com` (ADMIN, no linked
employee — demonstrates nullable `users.employee_id`), `divya.nair@oxp.com`
(HR_MANAGER), `priya.singh@oxp.com` (HR_PAYROLL_MANAGER),
`neha.patel@oxp.com` (HR_PAYROLL_USER), `john.dsouza@oxp.com`,
`aarav.mehta@oxp.com`, `sara.khan@oxp.com` (EMPLOYEE).

---

## 14. Known gaps & future hardening

Documented in the code/README, deliberately deferred for the 24h budget:

1. **Non-overlapping contract date ranges across statuses** — the partial
   unique index covers one-running; a `btree_gist` EXCLUDE constraint would
   reject *any* two contracts of any status overlapping in date range. The
   service already guards the common path (activate expires the previous
   one), so this is defense-in-depth.
2. **`contract_number` generation** (`max(id)+1`) is not gapless under
   concurrency — fine for display, would need a sequence for legal-grade
   numbering.
3. **Background jobs** — the missing-checkout sweep is a manual endpoint
   standing in for a cron job; payslip email is SMTP-based but there's no
   retry/queue story.
4. **Async driver** — documented option if concurrency ever matters.
5. **Audit log table** — `created_at/updated_at` + action stamps exist, but a
   generic audit trail ("who changed what when") is future work.
6. **Timezone model** — no per-employee tz column; the UTC-frame
   simplification is documented, not solved.
7. **Multi-company** — `company_id` exists as a placeholder but there's no
   tenant isolation logic beyond seeds defaulting to company 1.
8. **Frontend contract tightening** — `asEmployee` lenient normalization can
   be replaced with exact types now that the API is live.

---

## 15. Cheat sheet

| Decision | Chosen | Why | Rejected alternative |
|---|---|---|---|
| Web framework | FastAPI | Validation + OpenAPI + DI for free | Django (heavy/opinionated), Flask (hand-roll everything) |
| ORM | SQLAlchemy 2.0 sync | One engine for app+migrations+seeds, easy debugging | async SQLAlchemy/asyncpg (complexity, no scale need) |
| DB | PostgreSQL 16 | Partial unique indexes, pg_trgm, native ENUMs, EXCLUDE available | SQLite (constraints diverge), MySQL (weaker features), Mongo (no relational integrity) |
| Migrations | Alembic | Schema-as-code, one-command fresh setup | raw SQL scripts (no autogenerate, no downgrade) |
| Password hashing | bcrypt direct, 12 rounds | passlib unmaintained + breaks with bcrypt ≥4.1 | passlib, SHA-*, Argon2 (extra dep) |
| Tokens | PyJWT HS256, access/refresh split | Tiny, standard, type claim separation | python-jose, opaque server sessions |
| Money | NUMERIC(12,2) + Decimal + ROUND_HALF_UP | Float rounding unacceptable in payroll | FLOAT/DOUBLE |
| One running contract | Partial unique index + transaction | Structurally impossible to violate | app-only check (race-prone) |
| Leave balance | Derived SQL view | Stored totals drift | cached/stored running balance |
| Payslip lines | Snapshot rule code/name/category | Editing a rule must not rewrite paid payslips | live join to salary_rules |
| Attendance hours | Stored at check-out | Changing a schedule must not rewrite history | live recompute |
| Formulas | Restricted AST walker | Code-injection-safe | bare eval() |
| Deletes | Soft delete everywhere | History is the product | hard DELETE |
| Concurrency | Optimistic version_id | No held locks, friendly 409 retry | SELECT FOR UPDATE |
| Emails | VARCHAR + app lowercasing + unique | No extra extension | citext |
| PKs | BIGSERIAL | Simple, fast | UUID |
| Architecture | Monolith, 4 vertical slices, frozen shared files | Zero merge conflicts in 24h | microservices |
| Tests | Real Postgres + httpx ASGI | Constraints actually fire | SQLite |

---

## 16. Glossary

- **Vertical slice / module** — one domain's router+service+schema+tests,
  owned by one person, layered over the shared models.
- **Partial unique index** — a unique index over a subset of rows
  (`WHERE status = 'running'`); the DB-level guarantee behind one-running-
  contract.
- **Optimistic locking** — `version_id` column; writes carry the version they
  read and fail (409) if it changed — no locks held.
- **`AppException` hierarchy** — NotFound/Conflict/Validation/Forbidden/
  Unauthorized exceptions raised in services, mapped to `{detail, error_code}`
  by a global handler.
- **Kanban envelope** — `{groups: [{key, count, items}]}` returned by
  `GET /employees?group_by=...` instead of the paged `{items, total, ...}`.
- **Smart-button counts** — the `related: {contracts_count, attendance_count,
  time_off_count, allocations_count}` block in the employee form payload.
- **`version_id`** — the optimistic-lock counter; also the client contract for
  editing contracts (send the version you hold).
- **Sweep** — the manual endpoint stamping stale open attendance rows as
  `missing_checkout`; the scheduled-job equivalent.
- **Sentinel warning** — a `PayslipWarning` used as an idempotency marker
  (e.g. `SENT_AT` for emailed payslips).
- **`pg_trgm`** — the Postgres trigram extension powering the employee search
  GIN index (substring matching on `full_name`).
- **`ROUND_HALF_UP`** — the rounding mode for all money math (0.005 → 0.01),
  the conventional payroll rounding.

---

*Generated for Ameen from the actual codebase — `app/`, `tests/`,
`frontend/`, `alembic/`, README, and `00_ARCHITECTURE_AND_WORKFLOW.md`. If a
section contradicts the code, the code wins — flag it and we'll fix the doc.*