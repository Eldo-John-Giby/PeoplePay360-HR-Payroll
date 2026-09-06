# Ameen's Module — Explained End to End (Plain Language)

> This doc explains **only your slice** of PeoplePay360: Employees, Departments,
> Job Positions, Working Schedules, and Contracts — the files you own, the
> rules you enforce, and how a request travels from the browser all the way to
> Postgres and back. No payroll internals, no attendance internals — just your
> code, in simple terms.
>
> Files covered:
> ```
> app/schemas/employee.py          (the shapes of data going in and out)
> app/modules/employees/           (your routers + services)
> tests/test_employees.py          (your tests)
> ```
> Plus the small amount of shared code you **depend on** (explained in §3).

---

## 1. The big picture in one paragraph

Your module is the **HR master data** of the app: it answers "who works here,
what's their department/job/schedule, and under what contract?". Every other
part of the system builds on your data — payroll pays the person named on a
running contract, attendance records the person's check-ins, time off is
booked against the person's balance. So your job is to keep that master data
**clean**: no duplicate emails, no broken manager chains, no two running
contracts at once, no deleting things that history needs.

The golden rule of the codebase: **the database tables are owned by Eldo.**
You never create or change tables — you import the models and use them. All
your rules live in *your* service files.

---

## 2. The three layers, explained with a door analogy

Every endpoint in your module follows the same pattern:

```
Router  (the front door)
   │  checks: are you allowed in? (roles)
   │  reads: what did you ask for? (URL + body)
   │  calls ONE service function, returns whatever it gives back
   ▼
Service (the brain)
   │  checks every rule (does this exist? is it active? is this allowed?)
   │  does the work in one transaction
   ▼
Database (the filing cabinet)
   │  Postgres also has its own safety locks (constraints, indexes)
   ▼
Back to the browser as JSON
```

**Why split it this way?** The rules live in the service layer, not the
router, so you can test a rule (like "can't activate a contract out of
order") without the internet or a browser — just call the function directly.
Routers stay thin and dumb.

---

## 3. The shared pieces you rely on (briefly)

You don't own these files, but your code uses them every day. Here's what
they do in one line each:

| File | What it gives you |
|---|---|
| `app/core/database.py` | `get_db` — hands your service the database session. Every router function takes `db: Session = Depends(get_db)`. |
| `app/core/dependencies.py` | `require_roles(...)` — the bouncer. `require_roles("HR_MANAGER", "ADMIN")` returns 403 unless the logged-in user has one of those roles. `get_current_user` loads the logged-in user. |
| `app/core/exceptions.py` | The error types you raise: `NotFoundException` (404), `ConflictException` (409), `ValidationException` (422), `ForbiddenException` (403). Raise these; a global handler turns them into `{"detail": "...", "error_code": "..."}` JSON. **Never raise raw HTTP errors from services.** |
| `app/models/employee.py` | The `Employee`, `Contract`, `EmployeeBankDetail` tables (read-only for you). |
| `app/models/organization.py` | The `Department`, `JobPosition`, `WorkingSchedule`, `WorkingScheduleLine`, `Company` tables (read-only). |
| `app/models/enums.py` | The fixed option lists: `EmployeeStatus` (active/inactive/terminated), `ContractStatus` (draft/running/expired/cancelled), `EmployeeType`, `ScheduleType`. |

One habit to keep: **if you ever need a column that doesn't exist, you ask
Eldo for a migration. You do not edit the models.** That's the rule that
keeps four people's work from colliding.

---

## 4. Your file map — what lives where

```
app/schemas/employee.py
    All the request/response shapes (see §9).

app/modules/employees/
    service.py                The shared toolbox (see §7)
    router.py                 Just glues the 5 sub-routers together
    router_department.py      + service_department.py     → Departments
    router_job_position.py    + service_job_position.py   → Job Positions
    router_schedule.py        + service_schedule.py       → Working Schedules
    router_employee.py        + service_employee.py       → Employees
    router_contract.py        + service_contract.py       → Contracts

tests/test_employees.py       Your tests (see §10)
```

**Why split per entity?** Each file stays small enough to hold in your head,
and the shared `service.py` holds only helper functions that the entity
services import — never the other way around, so there are no circular
imports.

---

## 5. Departments — end to end

### What it is
A department is a box in the org chart. Departments can be nested (a
department can have a parent department), so the table points at itself:
`parent_department_id`.

### The endpoints (all HR-only)

| Action | URL | What happens |
|---|---|---|
| List | `GET /departments` | Paginated; filters `?search=&parent_id=&is_active=` |
| One | `GET /departments/{id}` | 404 if missing |
| Create | `POST /departments` | 201 on success |
| Edit | `PATCH /departments/{id}` | Partial update |
| Delete | `DELETE /departments/{id}` | **Soft delete** — see below |

### The rules, in plain words

1. **Your parent must exist and be switched on.** `parent_department_id` pointing
   at nothing → 404; pointing at a deactivated department → 422.
2. **No duplicate names.** Two departments with the same name under the same
   company → 409 "Department 'X' already exists."
3. **No family loops.** A department can't be its own parent, and you can't
   make A the parent of B while B is already the parent of A. The code checks
   by *walking up the family tree* from the proposed new parent, and stops
   after 20 hops so corrupt data can't cause an infinite loop (this is
   `would_create_cycle` in the shared toolbox — §7).
4. **Deleting is really "switching off".** `DELETE` sets `is_active = false`.
   But before it does, it counts how many **active employees** and **active
   job positions** still sit under the department. If either count is above
   zero → **409 with the counts in the message** ("it still has 3 active
   employee(s) and 1 active job position(s)"). The idea: HR must fix the
   people first, never silently orphan them.

### Why soft delete instead of a real delete?
Because history is the product. A department that existed in March still
appears on March's contracts and payslips; deleting the row would break every
old record that points at it. Turning it off keeps the past intact.

---

## 6. Job Positions — end to end

A job position is a role inside a department (e.g. "Software Engineer" under
"Engineering"). Much simpler than departments.

### The rules

1. **Belongs to a department** — the department must exist and be active
   (404 / 422).
2. **No duplicate titles inside the same department** → 409. (Same title in
   two different departments is fine.)
3. **Soft delete** sets `is_active = false`, but only if **no active
   employees** are still assigned to the position — otherwise 409 with the
   count ("Reassign them first").

That's it — no hierarchies, no cycles, nothing clever. Departments and job
positions are deliberately simple so the interesting stuff (employees,
contracts) stays readable.

---

## 7. The shared toolbox (`service.py`) — the helpers you use everywhere

| Helper | What it does | Where it's used |
|---|---|---|
| `HR_ROLES` | The set `{HR_MANAGER, HR_PAYROLL_USER, HR_PAYROLL_MANAGER, ADMIN}`. Plain `EMPLOYEE` is *not* in it. Every one of your routers does `require_roles(*HR_ROLES)`. | All routers |
| `paginate(page, page_size)` | Clamps: page at least 1, page size between 1 and 200 (default 20). `page=0` or `page_size=9999` can't crash anything. | Every list endpoint |
| `get_or_404(db, model, id, label)` | Fetches a row by id, raises 404 ("Employee 5 not found.") if missing. | Everywhere |
| `require_active(db, model, id, label)` | Like `get_or_404` but also demands the row is switched on → 422 if not. | Referencing departments/positions/schedules |
| `count_rows(db, model, ...)` | Counts rows matching conditions (used for the "how many active employees?" checks). | Soft-delete guards, related-summary |
| `compute_total_weekly_hours(lines)` | **Pure math, no database.** Adds up every schedule line's `(end − start) − break` in minutes, converts to hours, rounds to 2 decimals as a `Decimal` ("40.00"). Pure = testable with fake data. | Schedules, employee/contract detail views |
| `validate_schedule_lines(lines)` | Checks every line: day between 0–6, end after start, break shorter than the shift, and no two lines on the same day overlapping. Raises 422 with a friendly message. | Creating/replacing schedule lines |
| `would_create_cycle(db, model, id, parent_field, new_parent_id)` | Walks the parent/manager chain up to 20 levels; returns True if you'd create a loop. Raises 422 if the chain is deeper than 20 (possible corrupt data). | Departments, employee managers |
| `has_hr_access(user)` | True if the user holds any HR role. Used to let EMPLOYEE read their own record but nothing else. | Employee reads |

---

## 8. Working Schedules — end to end

### What it is
A working schedule is a **weekly pattern**: which days of the week someone
works, and from when to when, minus breaks. It's stored as a header
(`name`, `schedule_type`) plus one row per day in `working_schedule_lines`
(`day_of_week` 0=Mon..6=Sun, `start_time`, `end_time`, `break_minutes`).
Storing the pattern as separate rows (instead of a text blob like
"Mon-Fri 9-5") is deliberate — the payroll engine and attendance module can
then *compute* with it.

### The key idea: weekly hours are never stored

`total_weekly_hours` is **calculated on the fly** — the database has no
column for it. Why? Because it's a *derived* number: if HR edits a line, the
total must change too. If you stored it, you'd have to remember to update it
every time (and someone would forget, and then the UI would lie). So the
service just computes it with `compute_total_weekly_hours` whenever it's
needed.

### The rules

1. **Lines are validated before saving** (422 with a friendly message):
   - day must be 0–6 (the database also enforces this with a CHECK
     constraint — the rule exists twice on purpose, as a safety net),
   - end time must be after start time (also a DB CHECK),
   - break can't be longer than the shift itself (that would mean negative
     hours — only checked in code),
   - no two lines on the same day may overlap (9–13 and 12–17 on Monday is
     rejected; 9–13 and 13–17 is fine because they just touch).
2. **Replacing lines is all-or-nothing.** `PUT /working-schedules/{id}/lines`
   takes the *complete* new set of lines and swaps it in one go. **Why
   replace instead of add/remove one at a time?** Because partial edits can
   leave the schedule in a broken in-between state (e.g. you delete Monday's
   line but the request fails before adding Tuesday's). Replace-everything is
   the simplest correct behaviour. And validation runs *before* any change —
   a bad payload leaves the schedule exactly as it was.
3. **A schedule with zero days is allowed to exist, but can't be used.**
   HR might create the header first and add days later. But you can't assign
   a zero-day schedule to an employee or contract — that's a 422 "assign at
   least one working day first". (An employee with no working days would
   break payroll's day-counting.)
4. **Soft delete**, same pattern: 409 with counts if any **active employee**
   or **draft/running contract** still uses the schedule.

---

## 9. Employees — end to end

This is the heart of the app. The employee row is the "who", and everything
else hangs off it.

### What the employee form shows (the detail view)

`GET /employees/{id}` returns the full picture in one payload:

```
identity        full_name, work_email, phone
work info       department {id, name}, job_position {id, title}
manager         {id, full_name}          ← just id+name, not the whole manager
schedule        {id, name, total_weekly_hours}
type/status     employee_type, status, date_of_joining, work_location
smart buttons   related: {contracts_count, attendance_count,
                          time_off_count, allocations_count}
warnings        e.g. ["manager is inactive"]
```

**Why manager is only id + name:** the full manager object (with all their
own history) is heavy and almost never needed on this screen. Fetching it
anyway is the classic "N+1 over-fetching" waste. Same for the nested
summaries.

**The "smart buttons"** are the counts that appear as badges next to
"Contracts", "Attendance", "Time Off", "Allocations" in the UI. The frontend
shows a number and links to the filtered list. The endpoint
`GET /employees/{id}/related-summary` returns just those four counts.

### The rules

1. **Work email is unique, case-insensitively.** On every create/update the
   email is lowercased before saving, and a duplicate → 409 "already exists".
   (The database also has a unique constraint as the safety net.) Why
   lowercase? So `Ameen@Oxp.com` and `ameen@oxp.com` can't sneak in as two
   different people.
2. **Department, job position, and schedule must exist and be active.**
   Referencing a switched-off department → 422.
3. **A manager can't be yourself** (422) and **the management chain can't
   loop** (A manages B manages A → 422, same cycle-walk as departments).
4. **An *inactive* manager is allowed** — but the response carries a warning:
   `["manager is inactive"]`. **Why allow it?** Real HR sometimes moves
   people while the old manager's record is still being wound down. Blocking
   would get in the way; warning keeps them informed.
5. **Changing the employee's department/job does NOT rewrite their old
   contracts.** A contract is a snapshot of where the person worked *at
   signing time*. The employee row changes; history doesn't. (There's a test
   for exactly this.)
6. **Deleting an employee = setting status to `inactive`.** And there's a
   separate `POST /employees/{id}/terminate` that sets status to
   `terminated`. **Why two actions?** "Left the company" is a meaningful,
   auditable event, not just a delete. DELETE quietly switching someone off
   hides that intent.
7. **Employees can only see themselves.** If your role is `EMPLOYEE`:
   - `GET /employees/me` → your own record (404 if your account has no
     linked employee — e.g. admin accounts),
   - `GET /employees/me/contracts` → your own contracts,
   - `GET /employees/{id}` for *someone else's* id → **403**,
   - `GET /employees` (the list) → **403**. The list would leak the whole
     org to any employee, so it's HR-only.

### List + Kanban from one endpoint

`GET /employees` normally returns a **page** of rows
(`{items, total, page, page_size}`). But the UI also shows a **Kanban
board** (columns like Active / Inactive / Terminated, or by department).
Add `?group_by=status` (or `?group_by=department`) and the same endpoint
returns `{groups: [{key, count, items}]}` instead.

**Why does the Kanban mode return everything instead of one page?** Because a
board's column counts must be exact — if you paged the board, the "Active"
column would show only the first 20 active people and its count would be
wrong. A board needs the whole filtered set.

### Route order gotcha (memorize this)

`/employees/me` and `/employees/me/contracts` must be declared **before**
`/employees/{employee_id}` in the router file. Otherwise FastAPI tries to
read `"me"` as a number and answers 422 instead of the self-service route.
Same trick appears in the other modules (`/attendance/me`, `/payslips/me`).

---

## 10. Contracts — end to end (the interesting part)

### What it is
A contract is a deal: person X, in department D, job J, on schedule S, paid
wage W per month, from date A to date B (or open-ended). **Key idea: the
contract snapshots department/job/schedule at signing time** — those four
fields are copied onto the contract and never follow the employee's later
changes.

### The lifecycle (a state machine)

```
        ┌───────────── activate ─────────────┐
        ▼                                     ▼
   draft ──────────────► running ──────────────► expired
        │                (the current deal)     (finished)
        │
        └── cancel ──► cancelled (a draft that never happened)
```

Plain meanings:
- **draft** — a proposal being prepared. Can be edited freely.
- **running** — the deal currently in force. **Cannot be edited.** If you
  want a raise, you don't edit the running contract; you create a *new* draft
  with the new wage and activate it. The old one becomes history.
- **expired** — finished (either it reached its end date, or a newer
  contract replaced it).
- **cancelled** — a draft that was scrapped.

### Rule 1: there can be only ONE running contract per employee

This is enforced twice, deliberately:

1. **In the database** — a *partial unique index*: Postgres refuses to store
   two `running` rows for the same employee. ("Partial" means the rule only
   applies to running rows — drafts and expired ones are unlimited, because
   that's the history.)
2. **In the service** — when you activate a contract, the code first finds
   the employee's current running contract and expires it, *in the same
   transaction*.

**Why both?** The service logic handles the normal case; the database index
is the safety net for the race nobody can predict: two HR people clicking
"Activate" on two different drafts for the same employee at the same moment.
Only one can win; the loser gets a 409 "modified concurrently... please
refresh" instead of a corrupt database.

### Rule 2: activating a contract is one atomic transaction

Walk through a real example. Employee has:

- Contract A: starts 2024-01-15, no end date, `running`
- Contract B (draft): starts 2025-01-15

HR clicks **Activate** on B. In one go, the code:

1. Checks B is a draft (else 409).
2. Checks A's start date is **before** B's (if A started *on or after* B's
   start, that's activating out of order → 422 "Activate in chronological
   order — pick a later start date").
3. Sets A's status to `expired` and A's end date to `2025-01-14` — exactly
   **one day before B starts**.
4. Sets B's status to `running`.

**Why the −1 day dance?** So there's no gap (nobody covers 2025-01-10) and
no overlap (two contracts covering the same day). Payroll needs exactly one
applicable contract for any given date. A clean handoff is a real payroll
edge case, not decoration.

**What about the end date of the old contract — is it ever extended?** No.
The code only *tightens* it (sets it earlier if it was open or later). It
never pushes an old contract's end date forward — history stays honest.

### Rule 3: activating a future-dated contract is allowed

Pre-scheduled contracts are normal ("starting next month"). So B with start
2099-01-15 can be `running` today. But being `running` doesn't mean it's
*payable* yet — payroll checks the **date range** (`start_date <= period_date
<= end_date-or-open`), not just the status. There's a dedicated helper,
`get_applicable_contract`, that Steve's payroll calls, and a test proving a
future-running contract is NOT applicable to today.

### Rule 4: editing is draft-only, and stale edits are rejected

- `PATCH /contracts/{id}` works only while the contract is `draft`. Trying
  to change the wage on a `running` contract → 409 with the explanation
  "create a new draft contract with the new wage and activate it — history is
  preserved that way."
- Every contract read returns a `version_id` (starts at 1). Writes send the
  version they hold back. If someone else changed the contract in between,
  your version is stale → 409 "This contract was modified by someone else.
  Please refresh." This is *optimistic locking*: instead of holding the
  database open while you think, it just checks the version at write time.
  The database bumps the version automatically on every change.

### Rule 5: there is no delete button

Not for contracts, ever. Once a contract exists, it can only change status
(`cancel` for drafts, `expire` for running). **Why?** The spec says
"retain contract history" — a contract is a legal record. Deleting it would
erase the audit trail. If HR made a mistake, they cancel/expire and start a
new draft; the mistake stays visible as part of the story.

### Also worth knowing

- **You can't create a contract for a non-active employee** (422). Active
  people only; historical backfill is an Admin-level decision.
- **Contract numbers** look like `CON/2024/0003` — year plus a sequence. It's
  a display number, generated simply (max id + 1); good enough for a demo.
- **The list endpoint is built to avoid the N+1 trap.** Fetching 20
  contracts and then, for each one, separately fetching its employee,
  department, position, schedule, schedule-lines, and salary structure would
  be ~120 database queries. Instead `_read_dicts` gathers all the ids from
  the page and does **6 queries total** (one per related thing, with `IN
  (...)`). Small detail, big difference on the employee dashboard.

---

## 11. Your schemas (`app/schemas/employee.py`) in plain words

Pydantic schemas are the **contracts for data crossing the border** — what
the browser must send, and what it will get back. Your conventions:

| Convention | Meaning | Example |
|---|---|---|
| Separate `*Create` / `*Update` / `*Read` | A create schema, an update schema, and a read schema per entity — never one schema doing both jobs. | `EmployeeCreate`, `EmployeeUpdate`, `EmployeeDetail` |
| `*Update` fields all optional | PATCH sends only what changed. The service uses `model_dump(exclude_unset=True)` so it can tell "you didn't send manager_id" (leave it alone) from "you sent manager_id: null" (clear it). | `EmployeeUpdate`, `ContractUpdate` |
| `*Read` uses `from_attributes` | For simple entities the ORM object can serialize itself straight into the response. | `DepartmentRead`, `JobPositionRead` |
| `EmailStr` | Pydantic validates the email format before your service ever runs. | `EmployeeCreate.work_email` |
| `WageDecimal` | `condecimal(ge=0, max_digits=12, decimal_places=2)` — money must be ≥ 0, max 12 digits, 2 decimal places. No floats. | `ContractCreate.wage_monthly` |
| `Paginated[T]` | The standard list envelope `{items, total, page, page_size}`. | All list endpoints |
| `GroupedList[T]` / `Group[T]` | The Kanban envelope `{groups: [{key, count, items}]}`. | `GET /employees?group_by=` |

---

## 12. Your tests, explained

`tests/test_employees.py` — pytest + a real Postgres database, hitting the
real app through an in-process HTTP client (`httpx.ASGITransport`).

**How the test setup works:**
- A throwaway Postgres (`TEST_DATABASE_URL`, default port 5433) gets the full
  schema created once per test session, then **every table is wiped between
  tests** (`TRUNCATE ... RESTART IDENTITY CASCADE`) so each test starts
  clean.
- The app's database dependency is swapped for the test session, so the tests
  exercise the real routers, real role checks, real exception handlers.
- **Why a real Postgres and not SQLite?** Because your rules lean on
  database features SQLite doesn't behave identically for — the partial
  unique index (one running contract) and the CHECK constraints. A test suite
  that passes on SQLite but fails on the real database is worse than no
  tests.

**The key tests and the plain-English point of each:**

| Test | Proves |
|---|---|
| `test_create_department_job_position_employee_chain` | The happy path: build a department → a position under it → an employee in it, all 201s, and the smart-button counts start at zero. |
| `test_duplicate_work_email_conflict` | Duplicate emails (even `DUP@OXP.COM` vs `dup@oxp.com`) → 409, not a crash. |
| `test_activate_second_contract_expires_first` | Activate A, then B → A becomes `expired` with `end_date = B.start − 1 day`, B is `running`, and the database really holds exactly one running contract. |
| `test_activate_out_of_order_rejected` | Activating a contract whose start is earlier than (or equal to) the running one → 422. |
| `test_employee_role_cannot_list_but_can_read_me` | EMPLOYEE: list → 403, `/me` → 200, no linked employee → 404. |
| `test_schedule_overlapping_lines_rejected` | Overlapping same-day lines → 422; *touching* ranges (9–13, 13–17) are fine. |
| `test_department_self_cycle_rejected` | Self-parent and two-hop loops → 422. |
| `test_soft_delete_department_with_active_employees_conflict` | Deleting a department that still has active staff → 409 with counts in the message. |
| `test_patch_running_contract_wage_rejected` | Changing wage on a running contract → 409 (you must create + activate a new draft instead). |
| `test_contract_optimistic_lock_stale_version` | Sending a stale `version_id` → 409; sending the right one works and bumps the version. |
| `test_activate_future_start_allowed_and_resolver` | A future-dated contract can be `running` but is NOT applicable to today's payroll. |
| `test_employee_update_touches_only_employee_row` | Changing an employee's department doesn't rewrite their old contract's snapshot. |
| `test_manager_inactive_warning` | An inactive manager is accepted but surfaces as a warning in the response. |
| `test_compute_total_weekly_hours_pure` | The pure hours function: 5 × (9–18, 60min break) = 40.00; empty schedule = 0.00; no database needed. |

---

## 13. Quick gotchas — the five things most likely to bite you

1. **Route order:** `/employees/me` before `/employees/{employee_id}`, or
   "me" gets parsed as an id.
2. **`exclude_unset=True` vs explicit null:** when updating, always use
   `model_dump(exclude_unset=True)`; check *which fields were sent* before
   deciding whether "null" means "clear" or "ignore".
3. **Always lowercase emails before comparing/saving** — the unique
   constraint is on the lowercased value.
4. **Every contract write needs the `version_id` you read** — PATCH,
   activate, expire, cancel. Stale → 409, and that's correct behaviour, not a
   bug.
5. **Never edit the models.** Need a field? Ask Eldo. The services import
   models read-only; that's the deal that keeps the repo merge-safe.

---

## 14. One-page summary of your rules

| Entity | The rules in one line each |
|---|---|
| Department | parent exists+active; unique name per company; no loops (20-hop cap); soft delete blocked by active employees/positions (409 with counts) |
| Job Position | department exists+active; unique title per department; soft delete blocked by active employees |
| Working Schedule | lines: day 0–6, end>start, break<shift, no same-day overlap; replace-lines is atomic; zero-line schedules exist but can't be assigned; weekly hours always computed, never stored |
| Employee | unique lowercase email; active dept/position/schedule required; no self-manager, no manager loops; inactive manager OK with warning; PATCH never touches contracts; delete=inactive, terminate=explicit; EMPLOYEE sees only self; list is HR-only; Kanban = full set grouped |
| Contract | created as draft only; one running per employee (DB index + transaction); activate expires the previous running (−1 day); no out-of-order activation; future starts OK but not payable yet; draft-only edits; optimistic lock via version_id; no delete — cancel/expire only; snapshots dept/job/schedule at signing |

*That's your whole slice. Everything else in the repo (attendance, time off,
payroll, auth internals) builds on these five tables — keep them clean and
everyone else's job gets easier.*