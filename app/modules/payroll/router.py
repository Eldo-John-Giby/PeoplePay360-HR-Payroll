"""Payroll module endpoints (Steve's slice) — salary structures/rules,
payrun wizard + lifecycle, payslips + PDF.

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

# Roles ----------------------------------------------------------------
PAYROLL_READ = require_roles("HR_PAYROLL_USER", "HR_PAYROLL_MANAGER", "ADMIN")
PAYROLL_WRITE_STRUCTURES = require_roles("HR_PAYROLL_MANAGER", "ADMIN")


# ---------------------------------------------------------------------------
# Salary Rules
# ---------------------------------------------------------------------------


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
    return service.create_salary_rule(db, payload)


@router.get("/salary-rules/{rule_id}", response_model=SalaryRuleRead)
def get_salary_rule(
    rule_id: int,
    _: User = Depends(PAYROLL_READ),
    db: Session = Depends(get_db),
):
    return service.get_salary_rule(db, rule_id)


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
    """Soft delete (is_active=False); hard deletes are RESTRICT-protected."""
    service.delete_salary_rule(db, rule_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Salary Structures
# ---------------------------------------------------------------------------


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
    return service.create_salary_structure(db, payload)


@router.get("/salary-structures/{structure_id}", response_model=SalaryStructureRead)
def get_salary_structure(
    structure_id: int,
    _: User = Depends(PAYROLL_READ),
    db: Session = Depends(get_db),
):
    """Returns nested ordered `rules: [{sequence, rule: {...}}]`."""
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
    """Atomically replaces the full ordered rule list (409 on duplicates)."""
    return service.replace_structure_rules(
        db, structure_id, [r.model_dump() for r in payload.rules]
    )


# ---------------------------------------------------------------------------
# Payrun wizard
# ---------------------------------------------------------------------------


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
    """Wizard Step 2 — creates the Payrun (draft) + payrun_employees."""
    return service.create_payrun(db, payload, current_user)


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
    return service.get_payrun(db, payrun_id)


# ---------------------------------------------------------------------------
# Payrun lifecycle
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
    return service.validate_payrun(db, payrun_id)


@router.post("/payruns/{payrun_id}/mark-paid", response_model=MarkPaidResult)
def mark_paid(
    payrun_id: int,
    _: User = Depends(PAYROLL_READ),
    db: Session = Depends(get_db),
):
    """Only from validated; idempotency guard -> 409 on a second call."""
    return service.mark_paid(db, payrun_id)


@router.post("/payruns/{payrun_id}/cancel", response_model=CancelResult)
def cancel_payrun(
    payrun_id: int,
    _: User = Depends(PAYROLL_READ),
    db: Session = Depends(get_db),
):
    """Only from draft/computed — validated/paid runs are historical records."""
    return service.cancel_payrun(db, payrun_id)


@router.post("/payruns/{payrun_id}/send-payslips", response_model=SendPayslipsResult)
def send_payslips(
    payrun_id: int,
    _: User = Depends(PAYROLL_READ),
    db: Session = Depends(get_db),
):
    """Bulk-email payslips; per-employee results; idempotent (no double send)."""
    return service.send_payslips(db, payrun_id)


# ---------------------------------------------------------------------------
# Payslips
# ---------------------------------------------------------------------------


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
    return service.get_my_payslips(db, current_user, page=page, page_size=page_size)


@router.get("/payslips/{payslip_id}", response_model=PayslipRead)
def get_payslip(
    payslip_id: int,
    _: User = Depends(PAYROLL_READ),
    db: Session = Depends(get_db),
):
    """Full breakdown: lines + warnings."""
    return service.get_payslip(db, payslip_id)


@router.get("/payslips/{payslip_id}/pdf")
def get_payslip_pdf(
    payslip_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Streams the payslip PDF. Payroll roles: any payslip. EMPLOYEE: own
    payslips only (403 otherwise)."""
    from fastapi.responses import Response as FastAPIResponse

    pdf_bytes, filename = service.get_payslip_pdf(db, payslip_id, current_user)
    return FastAPIResponse(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )