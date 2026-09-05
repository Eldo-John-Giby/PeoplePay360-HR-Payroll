# PeoplePay360 — Attendance & Time Off frontend (Ambuj's slice)

React + TypeScript SPA (Vite) for the **Attendance + Time Off** module. Built
against the Attendance/TimeOff API and the shared auth endpoints only.

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
| Employee directory + per-employee cross-links | HR | Ameen's `/employees` (graceful empty state until it lands) |
| Time off requests — create, approve/refuse/cancel | all / HR | `/time-off/requests` + actions |
| Balances | EMPLOYEE `/me`, HR filtered | `/time-off/balances` |
| Time off types — config + create/deactivate | HR writes, all read | `/time-off/types` |
| Allocations — grant + approve/refuse | HR | `/time-off/allocations` |

The Employee directory screen (nav, HR only) wraps Ameen's `/employees` and
shows an explanatory empty state until his slice merges — the rest of the app
is unaffected. Payroll/payslips/dashboard (Steve) stays disabled in the nav
until the shared OpenAPI contract lands. Where a form needs an employee, it
takes a numeric employee id for now; ids are visible in the HR lists, and the
directory deep-links `?employee=<id>` into attendance and "request on behalf"
forms once populated.

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
`divya.nair@oxp.com` (HR_MANAGER), `john.dsouza@oxp.com` / `aarav.mehta@oxp.com`
(EMPLOYEE).

## Notes

- Statuses are computed server-side; the UI only renders them. Missing
  checkouts appear after end-of-day + grace (2h), either via the HR sweep
  button or automatically on read.
- Times are sent as naive local values and interpreted in the backend's UTC
  frame (documented in `app/modules/attendance_timeoff/service.py`).
