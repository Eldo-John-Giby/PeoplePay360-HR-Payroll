# PeoplePay360 — web frontend (React + Vite + TypeScript)

React + TypeScript SPA (Vite) covering Attendance + Time Off (Ambuj's slice),
Employee directory (Ameen's API) and the full Payroll section (Steve's
endpoints: payrun wizard + lifecycle, payslips + PDF, salary rules /
structures config, dashboard KPIs + charts).

## Run

Backend first (see repo root README):

```bash
docker compose up -d db
alembic upgrade head
python -m app.seed.seed_data
uvicorn app.main:app --reload          # API on :8000
```

Then:

```bash
cd frontend
npm install
npm run dev                            # http://localhost:5173
```

The Vite dev server proxies `/api/*` to `http://localhost:8000`. For another
backend, set `VITE_BACKEND_URL` (dev proxy target) or build against a real
URL with `VITE_API_URL`:

```bash
npm run build                          # typecheck + production bundle
```

## What's wired (against own API only)

| Screen | Who | Backed by |
|---|---|---|
| Login + role-based nav | all | `POST /auth/login`, `GET /auth/me` |
| Attendance — check-in/out + month summary | EMPLOYEE | `/attendance/me`, `/attendance/check-in`, `/attendance/{id}/check-out`, `/attendance/{id}/summary` |
| Attendance — list/filters (status/date/manual/id), manual entry, per-row correction, EOD sweep | HR | `/attendance`, `/attendance/sweep-missing-checkouts` |
| Employee directory + per-employee cross-links | HR | Ameen's `/employees` |
| Create Account (provision a login, link employee) | ADMIN | `POST /auth/users` (no public self-signup — HR issues credentials) |
| Time off requests — create, approve/refuse/cancel | all / HR | `/time-off/requests` + actions |
| Balances | EMPLOYEE `/me`, HR filtered | `/time-off/balances` |
| Time off types — config + create/deactivate | HR writes, all read | `/time-off/types` |
| Allocations — grant + approve/refuse | HR | `/time-off/allocations` |
| Payroll overview — composable filters (period/department/type/company) driving 5 KPI cards, dept bars, monthly trend, payslip status, payroll alerts, attendance + time-off + department overviews | payroll roles | `/dashboard/*` |
| Payruns — list + 2-step wizard + detail (compute/validate/mark-paid/cancel/send) | payroll roles | `/payroll/payruns*` |
| Payslips — register w/ filters + detail + PDF | payroll roles; EMPLOYEE `/me` + own PDF | `/payroll/payslips*` |
| Salary rules — global computation library | writes: manager/ADMIN | `/payroll/salary-rules` |
| Salary structures — ordered rule chains | writes: manager/ADMIN | `/payroll/salary-structures*` |

Nav + routes gate payroll by role: HR_PAYROLL_USER / HR_PAYROLL_MANAGER /
ADMIN get the whole section; EMPLOYEE gets a self-service "My Payslips" entry
(own payslips + PDF download, matching the backend RBAC); HR_MANAGER sees no
payroll entry. Where a form needs an employee, it takes a numeric employee
id for now; ids are visible in the HR lists.

## Demo script (attendance)

1. Log in as **john.dsouza@oxp.com** (EMPLOYEE) → Attendance → **Check in**,
   wait → **Check out**; the month's present/late/overtime cards update.
2. (HR) Log in as **divya.nair@oxp.com** → Attendance → filter **Late** or
   **Manual corrections**, then use **Correct** on a row to fix a late clock-in
   (hours + status recompute server-side and the row is stamped manual).

## Demo script (leave flow)

1. Log in as **john.dsouza@oxp.com** (EMPLOYEE) → Attendance → **Check in**,
   then **Check out**.
2. Time Off Requests → request e.g. 3 days of Paid Time Off (future dates).
3. Log in as **divya.nair@oxp.com** (HR_MANAGER) → Time Off Requests →
   **Approve** (or Refuse) the request.
4. My Balances / Balances show remaining drop by the approved duration
   (live view — nothing is pre-deducted).
5. Allocations → create a grant → approve it → balance reflects it.

Seed accounts (password `Password@123`): `admin@oxp.com` (ADMIN),
`neha.patel@oxp.com` (HR_PAYROLL_USER — payroll read + run actions),
`divya.nair@oxp.com` (HR_MANAGER — time-off/HR, no payroll),
`john.dsouza@oxp.com` / `aarav.mehta@oxp.com` (EMPLOYEE).

Payroll demo: log in as `neha.patel@oxp.com` → Payroll → Payruns → open the
seeded September run (id 2) → **Compute** → **Validate** is blocked by Kiran
Joshi's `missing_contract` warning → Payslips → **PDF**. Salary config
writes (new rule / structure chains) need `admin@oxp.com`.

## Notes

- Statuses are computed server-side; the UI only renders them. Missing
  checkouts appear after end-of-day + grace (2h), either via the HR sweep
  button or automatically on read.
- Times are sent as naive local values and interpreted in the backend's UTC
  frame (documented in `app/modules/attendance_timeoff/service.py`).
