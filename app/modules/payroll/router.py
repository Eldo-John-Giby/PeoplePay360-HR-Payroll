"""Payroll module endpoints (Steve's slice) — salary structures/rules,
payrun wizard + lifecycle, payslips + PDF.

CONNECTIONS MAP (read this first):
- MOUNTED BY: app/main.py (FROZEN, Eldo's) -> prefix="/api/v1/payroll",
  so every route below is reachable as /api/v1/payroll/...
- WHAT I DO: thin HTTP layer only — parse request, enforce RBAC via
  Depends(...), call ONE function in service.py, return. NO business logic
  lives here (arch §4.3) so service.py stays unit-testable without HTTP.
- CALLS: every function name here mirrors service.py (list_salary_rules ->
  service.list_salary_rules, compute_payrun -> service.compute_payrun, ...).
- AUTH: Depends(get_current_user) or Depends(require_roles(...)) from
  app/core/dependencies.py; role gates defined as the PAYROLL_READ /
  PAYROLL_WRITE_STRUCTURES constants below and reused by every route.
- ORDER MATTERS: /payslips/me is declared BEFORE /payslips/{payslip_id}
  (FastAPI matches in declaration order; 'me' would otherwise be captured
  as an int path param and 422). Same for /payruns/draft-scope before
  /payruns/{id}-style routes.

Routers stay thin (arch doc §4.3): parse request -> call one service
function -> return. All business rules live in service.py.

RBAC (arch doc §4.7 — the most nuanced split in the system):
- EMPLOYEE        : only GET /payslips/me and GET /payslips/{id}/pdf (own)
- HR_MANAGER      : NO payroll access at all — excluded from every route here
- HR_PAYROLL_USER : read payruns/payslips + structures/rules; create/update
                    payruns & payslips; NO write on structures/rules
- HR_PAYROLL_MANAGER / ADMIN: everything (full CRUD everywhere)
"""

from datetime import date

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.dependencies import get_current_user, require_roles
from app.models.auth import User
from app.models.enums import PayrunStatus, SalaryRuleCategory
from app.schemas.payroll import (
    CancelResult,
    ComputeResult,
    DraftScopeResponse,
    MarkPaidResult,
    Page,
    PayrunCreate,
    PayrunRead,
    PayrunScope,
    PayrunSummary,
    PayslipRead,
    PayslipSummaryItem,
    SalaryRuleCreate,
    SalaryRuleRead,
    SalaryRuleUpdate,
    SalaryStructureCreate,
    SalaryStructureRead,
    SalaryStructureRulesReplace,
    SalaryStructureSummary,
    SalaryStructureUpdate,
    SendPayslipsResult,
    ValidateResult,
)

from . import service

router = APIRouter()

# ---------------------------------------------------------------------------
# RBAC gate factories (arch doc §4.7 — the most nuanced split in the system)
# ---------------------------------------------------------------------------
# require_roles(...) returns a FastAPI dependency that 401s unauthenticated
# callers and 403s users who hold NONE of the listed roles (roles OR across a
# user's multiple roles). Two gates are enough for the whole module:
#
#   PAYROLL_READ             -> every payroll read/write EXCEPT structure
#                               writes (payrun/payslip operations). This is
#                               deliberately open to HR_PAYROLL_USER.
#   PAYROLL_WRITE_STRUCTURES -> ONLY salary structure/rule writes. HR_PAYROLL_USER
#                               is excluded here (read-only config) per spec.
#
# EMPLOYEE and HR_MANAGER never appear: EMPLOYEE gets only the two self-service
# payslip routes below (gated by get_current_user + ownership check in
# service.can_access_payslip); HR_MANAGER has NO payroll access at all.
# ---------------------------------------------------------------------------
PAYROLL_READ = require_roles("HR_PAYROLL_USER", "HR_PAYROLL_MANAGER", "ADMIN")
PAYROLL_WRITE_STRUCTURES = require_roles("HR_PAYROLL_MANAGER", "ADMIN")


# ---------------------------------------------------------------------------
# Salary Rules — /api/v1/payroll/salary-rules
# ---------------------------------------------------------------------------
# One atomic computation line item (fixed / % of another rule / formula).
# CRUD, gated by the RBAC constants above. All heavy lifting (uniqueness
# checks, soft-delete, method-consistency re-validation) lives in
# service.py:list/create/get/update/delete_salary_rule. NOTE for the demo:
# HR_PAYROLL_USER must get 403 here on writes but 200 on reads.


@router.get("/salary-rules", response_model=Page[SalaryRuleRead])
def list_salary_rules(
    _: User = Depends(PAYROLL_READ),
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    code: str | None = None,
    category: SalaryRuleCategory | None = None,
    is_active: bool | None = None,
):
    return service.list_salary_rules(
        db, page=page, page_size=page_size, code=code,
        category=category, is_active=is_active,
    )


@router.post(
    "/salary-rules",
    response_model=SalaryRuleRead,
    status_code=status.HTTP_201_CREATED,
)
def create_salary_rule(
    payload: SalaryRuleCreate,
    _: User = Depends(PAYROLL_WRITE_STRUCTURES),
    db: Session = Depends(get_db),
):
    # MANAGER/ADMIN only. SalaryRuleCreate's model_validator already enforces
    # "exactly one of amount/percentage/formula matching computation_method"
    # with a friendly 422 before this ever hits Postgres (see schemas).
    return service.create_salary_rule(db, payload)


@router.get("/salary-rules/{rule_id}", response_model=SalaryRuleRead)
def get_salary_rule(
    rule_id: int,
    _: User = Depends(PAYROLL_READ),
    db: Session = Depends(get_db),
):
    return service.get_salary_rule(db, rule_id)


# PATCH semantics: all fields optional; the service layer re-validates the
# MERGED row against the DB CHECK constraint (a partial PATCH can't be fully
# validated by pydantic alone — see service._validate_rule_consistency).
@router.patch("/salary-rules/{rule_id}", response_model=SalaryRuleRead)
def update_salary_rule(
    rule_id: int,
    payload: SalaryRuleUpdate,
    _: User = Depends(PAYROLL_WRITE_STRUCTURES),
    db: Session = Depends(get_db),
):
    return service.update_salary_rule(db, rule_id, payload)


@router.delete("/salary-rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_salary_rule(
    rule_id: int,
    _: User = Depends(PAYROLL_WRITE_STRUCTURES),
    db: Session = Depends(get_db),
):
    """Soft delete (is_active=False); hard deletes are RESTRICT-protected.
    A rule referenced by salary_structure_rules has ON DELETE RESTRICT at the
    DB level, so deactivating (is_active=False) is how you retire a rule that
    is still in use without breaking history."""
    service.delete_salary_rule(db, rule_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Salary Structures — /api/v1/payroll/salary-structures
# ---------------------------------------------------------------------------
# A named, ORDERED collection of salary rules that fully defines how pay is
# derived (the wireframe's "form view manages included rules and their
# execution sequence"). List/GET are payroll-read; writes are MANAGER/ADMIN.


@router.get("/salary-structures", response_model=Page[SalaryStructureSummary])
def list_salary_structures(
    _: User = Depends(PAYROLL_READ),
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    is_active: bool | None = None,
):
    return service.list_salary_structures(
        db, page=page, page_size=page_size, is_active=is_active
    )


@router.post(
    "/salary-structures",
    response_model=SalaryStructureRead,
    status_code=status.HTTP_201_CREATED,
)
def create_salary_structure(
    payload: SalaryStructureCreate,
    _: User = Depends(PAYROLL_WRITE_STRUCTURES),
    db: Session = Depends(get_db),
):
    # Creates the shell with NO rules yet — rules are attached afterwards
    # via PUT /salary-structures/{id}/rules (the ordered-rule replace flow).
    return service.create_salary_structure(db, payload)


@router.get("/salary-structures/{structure_id}", response_model=SalaryStructureRead)
def get_salary_structure(
    structure_id: int,
    _: User = Depends(PAYROLL_READ),
    db: Session = Depends(get_db),
):
    """Returns nested ordered `rules: [{sequence, rule: {...}}]`.
    The engine consumes exactly this ordering on Compute, so this is the
    canonical "how will this structure compute?" read for the UI."""
    return service.get_salary_structure(db, structure_id)


@router.patch("/salary-structures/{structure_id}", response_model=SalaryStructureRead)
def update_salary_structure(
    structure_id: int,
    payload: SalaryStructureUpdate,
    _: User = Depends(PAYROLL_WRITE_STRUCTURES),
    db: Session = Depends(get_db),
):
    return service.update_salary_structure(db, structure_id, payload)


@router.delete(
    "/salary-structures/{structure_id}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_salary_structure(
    structure_id: int,
    _: User = Depends(PAYROLL_WRITE_STRUCTURES),
    db: Session = Depends(get_db),
):
    """Soft delete — structures referenced by contracts stay as history."""
    service.delete_salary_structure(db, structure_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/salary-structures/{structure_id}/rules", response_model=SalaryStructureRead)
def replace_structure_rules(
    structure_id: int,
    payload: SalaryStructureRulesReplace,
    _: User = Depends(PAYROLL_WRITE_STRUCTURES),
    db: Session = Depends(get_db),
):
    """Atomically replaces the full ordered rule list (409 on duplicates).
    Replace-the-whole-list (never patch piecemeal) mirrors Ameen's working-
    schedule-lines pattern so sequence gaps/duplicates can't occur; the
    service layer deletes old junctions and inserts the new list in ONE
    transaction."""
    return service.replace_structure_rules(
        db, structure_id, [r.model_dump() for r in payload.rules]
    )


# ---------------------------------------------------------------------------
# Payrun wizard (2-step, stateless)
# ---------------------------------------------------------------------------
# The wireframe explicitly says Step 1 must NOT create the Payrun row — the
# frontend carries the scope forward to Step 2. No orphaned draft rows, no
# server state between the two calls.


@router.post("/payruns/draft-scope", response_model=DraftScopeResponse)
def draft_scope(
    scope: PayrunScope,
    _: User = Depends(PAYROLL_READ),
    db: Session = Depends(get_db),
):
    """Wizard Step 1 — collects scope only; does NOT create a Payrun row.
    Returns the eligible employee list for Step 2's selection screen."""
    return service.draft_scope(db, scope)


@router.post("/payruns", response_model=PayrunRead, status_code=status.HTTP_201_CREATED)
def create_payrun(
    payload: PayrunCreate,
    current_user: User = Depends(PAYROLL_READ),
    db: Session = Depends(get_db),
):
    """Wizard Step 2 — creates the Payrun (draft) + payrun_employees.
    current_user is captured here because the Payrun row stamps
    created_by_user_id for audit (arch §4.6)."""
    return service.create_payrun(db, payload, current_user)


# Payrun list supports the dashboard/list screens: filters on status,
# period overlap, and the department the run was scoped to.
@router.get("/payruns", response_model=Page[PayrunSummary])
def list_payruns(
    _: User = Depends(PAYROLL_READ),
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    status_filter: PayrunStatus | None = Query(default=None, alias="status"),
    period_start: date | None = None,
    period_end: date | None = None,
    department_filter_id: int | None = None,
):
    return service.list_payruns(
        db,
        page=page,
        page_size=page_size,
        status=status_filter,
        period_start=period_start,
        period_end=period_end,
        department_filter_id=department_filter_id,
    )


@router.get("/payruns/{payrun_id}", response_model=PayrunRead)
def get_payrun(
    payrun_id: int,
    _: User = Depends(PAYROLL_READ),
    db: Session = Depends(get_db),
):
    """Includes `payslips: [...]` summaries (id, employee, net, status,
    warning_count)."""
    # warning_count EXCLUDES the internal SENT_AT sentinels (service filters
    # them) so the UI only sees real payroll warnings.
    return service.get_payrun(db, payrun_id)


# ---------------------------------------------------------------------------
# Payrun lifecycle — the strict state machine: draft -> computed -> validated
# -> paid (plus cancelled). Each transition has guardrails enforced in the
# service layer (wrong current state => 409, blocking warnings => 409,
# double-click => 409/conflict). Routes are deliberately POST (state-changing
# actions), not PUT/PATCH on the resource.
# ---------------------------------------------------------------------------


@router.post("/payruns/{payrun_id}/compute", response_model=ComputeResult)
def compute_payrun(
    payrun_id: int,
    _: User = Depends(PAYROLL_READ),
    db: Session = Depends(get_db),
):
    """Runs the engine for every payrun_employee (idempotent: replaces lines
    on draft/computed payslips, skips finalized ones and reports them)."""
    return service.compute_payrun(db, payrun_id)


@router.post("/payruns/{payrun_id}/validate", response_model=ValidateResult)
def validate_payrun(
    payrun_id: int,
    _: User = Depends(PAYROLL_READ),
    db: Session = Depends(get_db),
):
    """Transitions payrun + payslips to validated — blocked (409) while any
    blocking warning (negative_net / missing_contract) is open."""
    # BLOCKING_WARNING_TYPES lives in service.py; missing_bank_details is
    # deliberately NOT blocking here (it blocks *sending*, not *amounts*).
    return service.validate_payrun(db, payrun_id)


@router.post("/payruns/{payrun_id}/mark-paid", response_model=MarkPaidResult)
def mark_paid(
    payrun_id: int,
    _: User = Depends(PAYROLL_READ),
    db: Session = Depends(get_db),
):
    """Only from validated; idempotency guard -> 409 on a second call.
    A paid run is a historical record — this endpoint is the point of no
    return in the lifecycle (Dashboard KPIs count only paid payslips)."""
    return service.mark_paid(db, payrun_id)


@router.post("/payruns/{payrun_id}/cancel", response_model=CancelResult)
def cancel_payrun(
    payrun_id: int,
    _: User = Depends(PAYROLL_READ),
    db: Session = Depends(get_db),
):
    """Only from draft/computed — validated/paid runs are historical records."""
    # The task spec: a validated/paid payrun is a historical record and can
    # NEVER be cancelled — the guard lives in service.cancel_payrun (409).
    return service.cancel_payrun(db, payrun_id)


@router.post("/payruns/{payrun_id}/send-payslips", response_model=SendPayslipsResult)
def send_payslips(
    payrun_id: int,
    _: User = Depends(PAYROLL_READ),
    db: Session = Depends(get_db),
):
    """Bulk-email payslips; per-employee results; idempotent (no double send)."""
    # Idempotency trick (no schema change allowed): service records a hidden
    # SENT_AT:... sentinel PayslipWarning per sent payslip and filters those
    # out of every read path — a second click sees the sentinel and skips.
    return service.send_payslips(db, payrun_id)


# ---------------------------------------------------------------------------
# Payslips — /api/v1/payroll/payslips
# ---------------------------------------------------------------------------
# The per-employee result documents. Filter by payrun/employee/status;
# detail includes full lines + warnings (PayslipRead nests them). "me" and
# "{id}/pdf" are the only routes reachable by the EMPLOYEE role.


# Payslip list: paginated; filters ?payrun_id=&employee_id=&status= (the
# status query param is aliased because `status` is also a common keyword).
@router.get("/payslips", response_model=Page[PayslipSummaryItem])
def list_payslips(
    _: User = Depends(PAYROLL_READ),
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    payrun_id: int | None = None,
    employee_id: int | None = None,
    status_filter: PayrunStatus | None = Query(default=None, alias="status"),
):
    return service.list_payslips(
        db, page=page, page_size=page_size, payrun_id=payrun_id,
        employee_id=employee_id, status=status_filter,
    )


@router.get("/payslips/me", response_model=Page[PayslipSummaryItem])
def my_payslips(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
):
    """Current employee's own payslips (EMPLOYEE self-service). Declared
    BEFORE /payslips/{id} so 'me' is never parsed as an id."""
    # Note: gated by plain get_current_user (any logged-in user incl.
    # EMPLOYEE), NOT by PAYROLL_READ — service.get_my_payslips resolves the
    # user's linked employee and returns only that employee's payslips.
    return service.get_my_payslips(db, current_user, page=page, page_size=page_size)


@router.get("/payslips/{payslip_id}", response_model=PayslipRead)
def get_payslip(
    payslip_id: int,
    _: User = Depends(PAYROLL_READ),
    db: Session = Depends(get_db),
):
    """Full breakdown: lines + warnings."""
    # Payroll-role route (EMPLOYEE gets only /me + own PDF). The service
    # loads employee, lines and warnings via selectinload (no N+1).
    return service.get_payslip(db, payslip_id)


@router.get("/payslips/{payslip_id}/pdf")
def get_payslip_pdf(
    payslip_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Streams the payslip PDF. Payroll roles: any payslip. EMPLOYEE: own
    payslips only (403 otherwise)."""
    # The response_model is intentionally omitted — FastAPI would try to
    # serialize bytes as JSON. Ownership enforcement happens in
    # service.get_payslip_pdf -> can_access_payslip (raises 403 for an
    # EMPLOYEE asking for someone else's payslip).
    from fastapi.responses import Response as FastAPIResponse

    pdf_bytes, filename = service.get_payslip_pdf(db, payslip_id, current_user)
    return FastAPIResponse(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )