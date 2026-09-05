"""Salary rule computation engine (Steve's slice) — the heart of the module.

Design goals (prompt §2, definition of done):
- Pure, DB-free core: `evaluate_formula` / `compute_rule_amount` / `run_engine`
  operate only on in-memory `SalaryRule` objects + a Decimal context dict, so
  they are unit-testable without FastAPI or a database.
- Formulas are stored as free text in the DB, so they are evaluated with a
  restricted AST walker (whitelisted names + a tiny set of functions) — never
  bare `eval()` (prompt §2.3.3).
- Every per-rule failure becomes a *payslip warning*, never a crash of the
  whole payrun.

Context keys injected before the loop (prompt §2.1 / §2.4):
- CONTRACT_WAGE       = applicable contract's wage_monthly (0 if none)
- WORKED_DAYS         = attendance-derived worked days in the period
- TOTAL_WORKING_DAYS  = expected working days from the working schedule
                        (0 when the schedule has no lines -> guarded, warn)

Rounding: every stored amount is quantized to 2 decimal places with
ROUND_HALF_UP (payroll convention; NUMERIC(12,2) columns).
"""

import ast
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.attendance import Attendance
from app.models.employee import Contract, Employee
from app.models.enums import (
    AttendanceStatus,
    ComputationMethod,
    ContractStatus,
    PayslipWarningType,
    SalaryRuleCategory,
)
from app.models.organization import WorkingSchedule
from app.models.payroll import SalaryRule, SalaryStructure, SalaryStructureRule

MONEY_QUANTUM = Decimal("0.01")

# Rules whose value is computed as part of gross salary (pre-deduction).
_GROSS_CATEGORIES = (
    SalaryRuleCategory.basic,
    SalaryRuleCategory.allowance,
    SalaryRuleCategory.gross,
)

# Whitelisted functions usable inside stored formulas.
_ALLOWED_FORMULA_FUNCS = {"min", "max", "round", "abs", "sum"}


class PayrollEngineError(Exception):
    """Controlled engine failure (forward reference, bad formula, unknown
    name...). Callers convert this into a payslip warning, never a 500."""


# ---------------------------------------------------------------------------
# Formula evaluation (restricted AST walker — NO bare eval)
# ---------------------------------------------------------------------------


def _to_decimal(value) -> Decimal:
    try:
        return Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise PayrollEngineError(f"Formula produced a non-numeric value: {value!r}") from exc


def _eval_node(node: ast.AST, context: dict[str, Decimal]) -> object:
    """Recursive restricted evaluator.

    Allowed: Name (from context only), Constant, BinOp (+ - * / // %),
    UnaryOp (+ -), Compare (< <= > >= == !=), BoolOp (and/or) and Call to the
    whitelisted functions. Anything else raises PayrollEngineError.
    """
    if isinstance(node, ast.Expression):
        return _eval_node(node.body, context)
    if isinstance(node, ast.Constant):
        # Floats in stored formulas (e.g. "0.12") must become exact Decimals
        # via str() — a raw float would smear binary noise into money math.
        if isinstance(node.value, float):
            return Decimal(str(node.value))
        return node.value
    if isinstance(node, ast.Name):
        if node.id not in context:
            raise PayrollEngineError(
                f"Unknown rule code '{node.id}' in formula (referenced before "
                "computation, or not part of this structure)."
            )
        return context[node.id]
    if isinstance(node, ast.BinOp):
        left = _to_decimal(_eval_node(node.left, context))
        right = _to_decimal(_eval_node(node.right, context))
        op = type(node.op)
        if op is ast.Add:
            return left + right
        if op is ast.Sub:
            return left - right
        if op is ast.Mult:
            return left * right
        if op is ast.Div:
            return left / right
        if op is ast.FloorDiv:
            return left // right
        if op is ast.Mod:
            return left % right
        raise PayrollEngineError(f"Unsupported binary operator: {op.__name__}.")
    if isinstance(node, ast.UnaryOp):
        operand = _to_decimal(_eval_node(node.operand, context))
        if isinstance(node.op, ast.USub):
            return -operand
        if isinstance(node.op, ast.UAdd):
            return operand
        raise PayrollEngineError(
            f"Unsupported unary operator: {type(node.op).__name__}."
        )
    if isinstance(node, ast.Compare):
        left = _to_decimal(_eval_node(node.left, context))
        for op_node, comp_node in zip(node.ops, node.comparators):
            right = _to_decimal(_eval_node(comp_node, context))
            if isinstance(op_node, ast.Lt) and not left < right:
                return False
            if isinstance(op_node, ast.LtE) and not left <= right:
                return False
            if isinstance(op_node, ast.Gt) and not left > right:
                return False
            if isinstance(op_node, ast.GtE) and not left >= right:
                return False
            if isinstance(op_node, ast.Eq) and not left == right:
                return False
            if isinstance(op_node, ast.NotEq) and not left != right:
                return False
            left = right
        return True
    if isinstance(node, ast.BoolOp):
        values = [_to_decimal(_eval_node(v, context)) for v in node.values]
        if isinstance(node.op, ast.And):
            return all(values)
        if isinstance(node.op, ast.Or):
            return any(values)
        raise PayrollEngineError("Unsupported boolean operator.")
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _ALLOWED_FORMULA_FUNCS:
            raise PayrollEngineError(
                "Formulas may only call whitelisted functions: "
                f"{', '.join(sorted(_ALLOWED_FORMULA_FUNCS))}."
            )
        if node.keywords:
            raise PayrollEngineError("Keyword arguments are not allowed in formulas.")
        # Keep raw values (round()'s ndigits must stay an int); the result
        # is coerced to Decimal afterwards.
        args = [_eval_node(a, context) for a in node.args]
        func_map = {
            "min": min,
            "max": max,
            "round": round,
            "abs": abs,
            "sum": sum,
        }
        try:
            return _to_decimal(func_map[node.func.id](*args))
        except (TypeError, ValueError, ZeroDivisionError, InvalidOperation, OverflowError) as exc:
            raise PayrollEngineError(f"Call to {node.func.id}() failed: {exc}") from exc
    raise PayrollEngineError(
        f"Unsupported expression construct in formula: {type(node).__name__}."
    )


def evaluate_formula(expression: str, context: dict[str, Decimal]) -> Decimal:
    """Parse + safely evaluate a stored formula against the rule context."""
    if not expression or not expression.strip():
        raise PayrollEngineError("Formula is empty.")
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise PayrollEngineError(f"Formula syntax error: {exc.msg}.") from exc
    try:
        value = _eval_node(tree.body, context)
    except PayrollEngineError:
        raise
    except (ZeroDivisionError, InvalidOperation, OverflowError, TypeError, ValueError) as exc:
        raise PayrollEngineError(f"Formula evaluation failed: {exc}") from exc
    return _to_decimal(value)


# ---------------------------------------------------------------------------
# Per-rule computation
# ---------------------------------------------------------------------------


def compute_rule_amount(rule: SalaryRule, context: dict[str, Decimal]) -> Decimal:
    """Compute ONE rule's amount against the accumulated context.

    Raises PayrollEngineError for unresolvable references (forward reference,
    nonexistent base code, malformed rule) — callers convert to warnings.
    """
    method = rule.computation_method
    if method == ComputationMethod.fixed:
        if rule.amount is None:
            raise PayrollEngineError(
                f"Rule '{rule.code}' is 'fixed' but has no amount."
            )
        return rule.amount
    if method == ComputationMethod.percentage:
        if rule.percentage is None or not rule.percentage_base_code:
            raise PayrollEngineError(
                f"Rule '{rule.code}' is 'percentage' but is missing percentage "
                "or percentage_base_code."
            )
        base_code = rule.percentage_base_code
        if base_code not in context:
            raise PayrollEngineError(
                f"Percentage rule '{rule.code}' references base '{base_code}' "
                "which is not computed yet (forward reference) or is not part "
                "of this structure."
            )
        return (rule.percentage / Decimal("100")) * context[base_code]
    if method == ComputationMethod.formula:
        if not rule.formula:
            raise PayrollEngineError(
                f"Rule '{rule.code}' is 'formula' but has no formula text."
            )
        return evaluate_formula(rule.formula, context)
    raise PayrollEngineError(
        f"Rule '{rule.code}' has unknown computation_method: {method!r}."
    )


# ---------------------------------------------------------------------------
# Pure engine loop
# ---------------------------------------------------------------------------


@dataclass
class EngineLine:
    salary_rule_id: int
    sequence: int
    code: str
    name: str
    category: SalaryRuleCategory
    amount: Decimal


@dataclass
class EngineResult:
    lines: list[EngineLine] = field(default_factory=list)
    # (PayslipWarningType, message) tuples — ready to persist as PayslipWarnings.
    warnings: list[tuple[PayslipWarningType, str]] = field(default_factory=list)
    gross_salary: Decimal = Decimal("0")
    net_salary: Decimal = Decimal("0")


def run_engine(
    rules: list[SalaryRule],
    base_context: dict[str, Decimal] | None = None,
) -> EngineResult:
    """Execute the ordered rule list against an initial context.

    Rules must be ordered by sequence (caller guarantees); the engine sorts
    defensively. Handles every edge case in prompt §2.5:
    - forward/cyclic percentage references -> warning, amount 0, keep going
    - base code not in this structure      -> warning, amount 0
    - formula referencing unknown names     -> warning, amount 0
    - no rules at all                       -> zeros + warning
    - negative net                          -> negative_net warning
    - missing explicit NET rule             -> fallback + warning
    """
    result = EngineResult()
    context: dict[str, Decimal] = dict(base_context or {})

    ordered = sorted(
        rules,
        key=lambda r: (
            getattr(r, "sequence", 0) if hasattr(r, "sequence") else getattr(r, "default_sequence", 0),
            getattr(r, "id", 0) or 0,
        ),
    )

    if not ordered:
        result.warnings.append(
            (PayslipWarningType.other, "Salary structure has no rules — payslip computed as zero.")
        )
        return result

    for rule in ordered:
        try:
            amount = compute_rule_amount(rule, context)
        except PayrollEngineError as exc:
            result.warnings.append(
                (PayslipWarningType.other, f"{exc} Computed as 0.")
            )
            amount = Decimal("0")
        except (ZeroDivisionError, InvalidOperation, OverflowError, TypeError, ValueError) as exc:
            result.warnings.append(
                (PayslipWarningType.other, f"Rule '{rule.code}' failed: {exc}. Computed as 0.")
            )
            amount = Decimal("0")

        amount = _quantize(amount)
        context[rule.code] = amount
        result.lines.append(
            EngineLine(
                salary_rule_id=rule.id,
                sequence=getattr(rule, "sequence", 0) if hasattr(rule, "sequence") else rule.default_sequence,
                code=rule.code,
                name=rule.name,
                category=rule.category,
                amount=amount,
            )
        )

    # -- Gross: explicit GROSS-category rule wins; else sum basic+allowance --
    gross_rules = [l for l in result.lines if l.category == SalaryRuleCategory.gross]
    if gross_rules:
        result.gross_salary = _quantize(gross_rules[0].amount)
    else:
        result.gross_salary = _quantize(
            sum(l.amount for l in result.lines if l.category in _GROSS_CATEGORIES)
        )

    # -- Net: explicit NET-category rule wins; else gross - deductions ------
    net_rules = [l for l in result.lines if l.category == SalaryRuleCategory.net]
    if net_rules:
        result.net_salary = _quantize(net_rules[0].amount)
    else:
        deductions = sum(
            l.amount
            for l in result.lines
            if l.category == SalaryRuleCategory.deduction
        )
        result.net_salary = _quantize(result.gross_salary - deductions)
        result.warnings.append(
            (
                PayslipWarningType.other,
                "Structure has no explicit NET rule — net computed as "
                "gross minus deduction lines.",
            )
        )

    if result.net_salary < 0:
        result.warnings.append(
            (
                PayslipWarningType.negative_net,
                f"Net salary is negative ({result.net_salary}). Payrun cannot "
                "be validated until resolved.",
            )
        )

    return result


def _quantize(value: Decimal) -> Decimal:
    return Decimal(value).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------------------
# Attendance / schedule helpers (read-only queries into Ameen's & Ambuj's data)
# ---------------------------------------------------------------------------

_PRESENT_STATUSES = (
    AttendanceStatus.present,
    AttendanceStatus.late,
    AttendanceStatus.overtime,
)


def count_worked_days(
    db: Session, employee_id: int, period_start: date, period_end: date
) -> Decimal:
    """Distinct attendance dates with status present/late/overtime in period."""
    count = db.scalar(
        select(func.count(func.distinct(func.date(Attendance.check_in))))
        .where(
            Attendance.employee_id == employee_id,
            Attendance.status.in_([s.value for s in _PRESENT_STATUSES]),
            func.date(Attendance.check_in) >= period_start,
            func.date(Attendance.check_in) <= period_end,
        )
    )
    return Decimal(count or 0)


def expected_working_days(
    db: Session, employee: Employee, period_start: date, period_end: date
) -> int:
    """Expected working days from the employee's working schedule lines.

    Days whose weekday is listed in the schedule count as expected. A schedule
    with no lines falls back to 0 (engine guards the resulting division).
    """
    schedule = db.get(WorkingSchedule, employee.working_schedule_id)
    if schedule is None or not schedule.lines:
        return 0
    dow_set = {line.day_of_week for line in schedule.lines}
    span = (period_end - period_start).days + 1
    return sum(
        1
        for i in range(span)
        if (period_start + timedelta(days=i)).weekday() in dow_set
    )


def _overlap_days(
    contract_start: date, contract_end: date | None, period_start: date, period_end: date
) -> int:
    eff_end = contract_end or date.max
    start = max(contract_start, period_start)
    end = min(eff_end, period_end)
    if end < start:
        return 0
    return (end - start).days + 1


def resolve_applicable_contract(
    db: Session, employee_id: int, period_start: date, period_end: date
) -> tuple[Contract | None, list[tuple[PayslipWarningType, str]]]:
    """Named edge case (prompt §2.2): pick the contract that applies to the
    payroll PERIOD, not merely the 'running' one.

    - A contract is applicable when its [start_date, end_date-or-open] range
      overlaps the payroll period AND status in ('running', 'expired') — an
      expired contract is the correct one for a *past* payrun period.
    - Prefer the contract whose range covers period_start; if several overlap
      (mid-period contract change is legal), pick the one covering the
      majority of the period and record a contract-change warning.
    - Returns (None, []) when no contract overlaps -> caller records a
      `missing_contract` warning and still creates a zero-value payslip.
    """
    contracts = list(
        db.scalars(
            select(Contract).where(
                Contract.employee_id == employee_id,
                Contract.status.in_([ContractStatus.running, ContractStatus.expired]),
            )
        ).all()
    )
    overlapping = [
        c
        for c in contracts
        if c.start_date <= period_end
        and (c.end_date is None or c.end_date >= period_start)
    ]
    if not overlapping:
        return None, []

    warnings: list[tuple[PayslipWarningType, str]] = []
    covering_start = [
        c
        for c in overlapping
        if c.start_date <= period_start
        and (c.end_date is None or c.end_date >= period_start)
    ]

    if len(covering_start) == 1:
        chosen = covering_start[0]
    elif len(covering_start) > 1:
        chosen = max(
            covering_start,
            key=lambda c: _overlap_days(c.start_date, c.end_date, period_start, period_end),
        )
        warnings.append(
            (
                PayslipWarningType.other,
                "Multiple contracts cover the payrun period start; using the "
                "one covering the most days.",
            )
        )
    else:
        chosen = max(
            overlapping,
            key=lambda c: _overlap_days(c.start_date, c.end_date, period_start, period_end),
        )
        warnings.append(
            (
                PayslipWarningType.other,
                "Payroll period spans a contract change; using the contract "
                "covering the majority of the period.",
            )
        )
    return chosen, warnings


# ---------------------------------------------------------------------------
# Per-employee compute orchestration
# ---------------------------------------------------------------------------


@dataclass
class ComputedPayslip:
    employee_id: int
    contract_id: int | None
    worked_days: Decimal
    gross_salary: Decimal
    net_salary: Decimal
    lines: list[EngineLine]
    warnings: list[tuple[PayslipWarningType, str]]


def compute_payslip_for_employee(
    db: Session, payrun, employee: Employee
) -> ComputedPayslip:
    """Run the full engine for one employee of a payrun (read-only queries;
    persistence happens in the service layer so idempotent recompute logic
    stays in one place)."""
    structure = db.get(SalaryStructure, payrun.salary_structure_id)
    if structure is None:
        raise PayrollEngineError(
            f"Salary structure {payrun.salary_structure_id} not found for payrun {payrun.id}."
        )

    structure_rules = list(
        db.scalars(
            select(SalaryStructureRule)
            .where(SalaryStructureRule.salary_structure_id == structure.id)
            .order_by(SalaryStructureRule.sequence, SalaryStructureRule.id)
        ).all()
    )
    rules: list[SalaryRule] = []
    warnings: list[tuple[PayslipWarningType, str]] = []
    for sr in structure_rules:
        if not sr.salary_rule.is_active:
            warnings.append(
                (
                    PayslipWarningType.other,
                    f"Rule '{sr.salary_rule.code}' is inactive and was skipped.",
                )
            )
            continue
        rules.append(sr.salary_rule)

    contract, contract_warnings = resolve_applicable_contract(
        db, employee.id, payrun.period_start, payrun.period_end
    )
    warnings.extend(contract_warnings)

    if contract is None:
        warnings.append(
            (
                PayslipWarningType.missing_contract,
                "No contract covers the payrun period — salary computed as "
                "zero and Validate is blocked until resolved.",
            )
        )

    worked_days = count_worked_days(db, employee.id, payrun.period_start, payrun.period_end)
    total_working_days = expected_working_days(db, employee, payrun.period_start, payrun.period_end)

    base_context = {
        "CONTRACT_WAGE": contract.wage_monthly if contract else Decimal("0"),
        "WORKED_DAYS": worked_days,
        "TOTAL_WORKING_DAYS": Decimal(total_working_days),
    }

    result = run_engine(rules, base_context)
    warnings.extend(result.warnings)

    return ComputedPayslip(
        employee_id=employee.id,
        contract_id=contract.id if contract else None,
        worked_days=worked_days,
        gross_salary=result.gross_salary,
        net_salary=result.net_salary,
        lines=result.lines,
        warnings=warnings,
    )