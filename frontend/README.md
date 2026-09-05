# PeoplePay360 — frontend (Attendance & Time Off + Payroll console)

React + TypeScript SPA (Vite) for the PeoplePay360 HR console: **Attendance,
Time Off, Employees, Contracts, Working Schedules, Payroll** (payruns /
payslips / dashboard / salary config) and **Admin** user management.

## Run

Backend first (see repo root README or use `dev.py` on machines without
Docker):

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

## What's wired (all live API data — no mock data)

| Screen | Who | Backed by |
|---|---|---|
| **Landing page** (entry point, before login) | public | — |
| Login + role-based nav | all | `POST /auth/login`, `GET /auth/me` |
| Attendance — check-in/out + month summary | EMPLOYEE | `/attendance/me`, `/attendance/check-in`, `/attendance/{id}/check-out`, `/attendance/{id}/summary` |
| Attendance — list/filters, manual entry, per-row correction, EOD sweep | HR | `/attendance`, `/attendance/sweep-missing-checkouts` |
| Employee directory + per-employee cross-links | HR | Ameen's `/employees` |
| **Contracts** — list/filters, create/edit (draft), activate/expire/cancel, overlap warning | HR; EMPLOYEE sees own (read-only) | `/contracts`, `/contracts/{id}/activate|expire|cancel`, `/employees/me/contracts` |
| **Working schedules** — list + 7-row weekly grid (live total hours) | HR | `/working-schedules`, `/working-schedules/{id}/lines` |
| Time off requests — create, approve/refuse/cancel | all / HR | `/time-off/requests` + actions |
| Balances | EMPLOYEE `/me`, HR filtered | `/time-off/balances` |
| Time off types — config + create/deactivate | HR writes, all read | `/time-off/types` |
| Allocations — grant + approve/refuse | HR | `/time-off/allocations` |
| **Payruns** — list + 2-step wizard (scope → employee selection) | payroll roles | `/payroll/payruns/draft-scope`, `/payroll/payruns` |
| **Payrun processing** — Compute → Validate → Mark Paid → Send Payslips, warnings panel | payroll roles | `/payroll/payruns/{id}/compute|validate|mark-paid|send-payslips` |
| **Payslips** — list + detail (computation table, print view) | payroll roles; EMPLOYEE own via `/payslips/me` | `/payroll/payslips`, `/payroll/payslips/{id}` |
| **Payroll dashboard** — KPIs, charts (Recharts), alerts, attendance/time-off overviews, department table | payroll roles | `/dashboard/kpis`, `/dashboard/salary-by-department`, `/dashboard/monthly-net-salary-trend`, `/dashboard/attendance-overview`, `/dashboard/time-off-overview`, `/dashboard/payroll-alerts` |
| **Salary structures** — ordered rule list (reorderable) | `HR_PAYROLL_MANAGER`/`ADMIN` write; `HR_PAYROLL_USER` read-only | `/payroll/salary-structures`, `/payroll/salary-structures/{id}/rules` |
| **Salary rules** — adaptive form (fixed / percentage / formula) | same RBAC as structures | `/payroll/salary-rules` |
| Accounts — provision login, link employee | ADMIN | `/auth/users`, `PATCH /auth/users/{id}` |
| **Admin / Settings** — user list + role assignment | ADMIN | `/auth/users`, `PATCH /auth/users/{id}/roles` |

RBAC rules are enforced by the backend; the frontend mirrors them for the
nav (e.g. payroll sub-nav shows only for `HR_PAYROLL_USER` +; HR_MANAGER
never sees payroll screens, per spec).

## Demo scripts

### Attendance + time off
1. Log in as **john.dsouza@oxp.com** (EMPLOYEE) → Attendance → **Check in**,
   wait → **Check out**; month cards update.
2. Log in as **divya.nair@oxp.com** (HR_MANAGER) → Attendance → filter **Late**
   / **Manual corrections**, use **Correct** on a row (hours + status recompute
   server-side).
3. Time Off Requests → request Paid Time Off → approve as HR → Balances drop
   live (nothing is pre-deducted).

### Payroll (log in as **neha.patel@oxp.com** — HR_PAYROLL_USER)
1. **Payruns** → open the *September 2026* run → **Compute** (generates
   payslips + warnings — e.g. Kiran has no contract, Sneha no bank details).
2. **Validate** stays disabled while blocking warnings are open; once resolved,
   validate → **Mark Paid** → **Send payslips** (simulated email toast).
3. **Payslips** → open any → computation table (Basic → Allowances → Gross →
   Deductions → Net) + **Print payslip**.
4. **Dashboard** (payroll sub-nav) → KPI cards, charts, alerts, attendance &
   time-off overviews, department breakdown — all live from the API.

### Salary config + admin
- **Salary Rules** (neha can read; **priya.singh@oxp.com** — HR_PAYROLL_MANAGER
  — can create/edit: pick computation type and the form adapts).
- **Salary Structures** → edit rules order with ↑/↓ (sequence matters).
- **Admin** (admin@oxp.com) → change any user's role via dropdown.

Seed accounts (password `Password@123`): `admin@oxp.com` (ADMIN),
`divya.nair@oxp.com` (HR_MANAGER), `priya.singh@oxp.com` (HR_PAYROLL_MANAGER),
`neha.patel@oxp.com` (HR_PAYROLL_USER), `john.dsouza@oxp.com` /
`aarav.mehta@oxp.com` / `sara.khan@oxp.com` (EMPLOYEE).

## Notes

- Statuses (attendance, payrun lifecycle, salary categories) are computed
  server-side; the UI only renders them.
- Payrun compute/validate/mark-paid is a strict state machine
  (`draft → computed → validated → paid`); once paid the run is locked.
- The seed creates contracts for every employee (incl. an employee with both
  an expired and a running contract, one intern with **no** contract, one
  employee with **no bank details**) so every screen has real data to show.